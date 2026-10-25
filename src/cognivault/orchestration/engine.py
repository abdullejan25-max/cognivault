"""Agent A lifecycle: policy, bounded Agent B calls, validation, Gateway, readback."""
import asyncio
import inspect
import re
from copy import deepcopy
from datetime import datetime, timezone

from ..contracts import GatewayError
from .models import AdapterError, Receipt, Result, TRANSITIONS, fingerprint, identifier


class Engine:
    def __init__(self, registry, journal, handlers):
        self.registry, self.journal = registry, journal
        self.handlers = dict(handlers)
        self.pending_operations = set()

    async def run(self, task, agent_id=None, *, cancel=None, resume_result=None):
        # Snapshot mutable caller input before the first await.
        task = deepcopy(task)
        digest = task.digest()
        if agent_id is not None and not identifier(agent_id):
            raise ValueError('INVALID_AGENT_ID')
        # Bind automatic routing once; recovery must not silently select a new agent.
        if agent_id is None:
            with self.journal.lock(task.task_id):
                saved = self.journal.load(task.task_id)
                if saved and saved['adapter_id'] != 'unassigned':
                    agent_id = saved['adapter_id']
                else:
                    try:
                        agent_id = self.registry.select(task)
                    except ValueError as error:
                        if str(error) != 'AGENT_UNAVAILABLE':
                            raise
                        agent_id = 'unassigned'
        with self.journal.lock(task.task_id):
            result, response = None, None
            record = self.journal.load(task.task_id)
            if record is not None and (record.get("request_hash") != digest
                                       or record.get("adapter_id") != agent_id
                                       and not (record['state'] == 'BLOCKED' and record['adapter_id'] == 'unassigned')):
                raise ValueError("CONFLICT")
            if record is None:
                record = dict(schema=1, task_id=task.task_id, correlation_id=task.correlation_id,
                              request_hash=digest, adapter_id=agent_id, execution_scope="unknown",
                              adapter_version=None, state="RECEIVED", attempts=0, error_code=None,
                              events=["RECEIVED"], verified=False, commit_intent=False)
                record["created_at"] = datetime.now(timezone.utc).isoformat()
                self.journal.save(task.task_id, record)

            def receipt():
                return Receipt(task.task_id, task.correlation_id, record["state"], record["attempts"],
                               record["error_code"], agent_id, record["execution_scope"],
                               record["verified"], tuple(record["events"]),
                               tuple(record.get("artifacts", [])),
                               deepcopy(result.output) if type(result) is Result and record['commit_intent'] else None,
                               deepcopy(response), deepcopy(record.get('execution_evidence')))

            def move(state, code=None):
                if state != record["state"]:
                    if state not in TRANSITIONS[record["state"]]:
                        raise ValueError("INVALID_TRANSITION")
                    record["events"].append(state)
                    record["state"] = state
                record["error_code"] = code
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.journal.save(task.task_id, record)

            if record["state"] in {"COMPLETED", "CANCELLED", "FAILED"}:
                return receipt()
            if record.get("business_review") and record["state"] == "WAITING_FOR_REVIEW":
                return receipt()  # Candidate is saved; only the separate reviewer may promote it.
            if record["state"] == "WAITING_FOR_REVIEW" and not record["commit_intent"]:
                return receipt()  # New authorization requires a separate, reviewed task.

            try:
                handler = self.handlers.get(task.kind)
                if record["state"] in {"RECEIVED", "BLOCKED"}:
                    record['adapter_id'] = agent_id
                    move("PLANNED")
                if handler is None:
                    raise ValueError("UNSUPPORTED_TASK")
                if not handler.required_grants <= task.grants:
                    raise ValueError("PERMISSION_DENIED")
                if agent_id == 'unassigned':
                    move('BLOCKED', 'AGENT_UNAVAILABLE')
                    return receipt()
                adapter = self.registry.get(agent_id)
                if not self.registry.enabled(agent_id):
                    move('BLOCKED', 'AGENT_DISABLED')
                    return receipt()
                desc = adapter.descriptor
                if task.kind not in desc.kinds:
                    raise ValueError("CAPABILITY_UNAVAILABLE")
                if desc.data_scope == "external" and not task.external_allowed:
                    raise ValueError("EXTERNAL_TRANSFER_DENIED")
                if not task.required_capabilities <= desc.capabilities:
                    raise ValueError('CAPABILITY_UNAVAILABLE')
                if record["adapter_version"] not in (None, desc.version):
                    raise ValueError("ADAPTER_VERSION_CONFLICT")
                record.update(execution_scope=desc.data_scope, adapter_version=desc.version)
                if record["state"] == "PLANNED":
                    move("AUTHORIZED")

                if cancel is not None and cancel.is_set():
                    move("PARTIAL" if record["commit_intent"] else "CANCELLED", "CANCELLED")
                    return receipt()
                prepared = await self._bounded(handler.prepare(deepcopy(task)), task.timeout_s, cancel)
                if record["commit_intent"]:
                    if resume_result is None:
                        move("WAITING_FOR_REVIEW", "RESUME_RESULT_REQUIRED")
                        return receipt()
                    result = deepcopy(resume_result)
                    if type(result) is not Result or result.digest() != record.get("result_hash"):
                        move("WAITING_FOR_REVIEW", "RESUME_RESULT_MISMATCH")
                        return receipt()
                    result.validate(task, desc)
                    handler.validate(task, result, prepared)
                    record["verified"] = False
                    move("COMMITTING")
                else:
                    # A crash in RUNNING consumes an attempt. A validated but unwritten
                    # result can be regenerated because there was no commit intent.
                    if record["state"] == "VALIDATING":
                        move("WAITING_FOR_REVIEW", "INTERRUPTED_VALIDATION")
                        return receipt()
                    if record["attempts"] >= task.max_attempts:
                        move("RETRYABLE_FAILURE", "ATTEMPTS_EXHAUSTED")
                        return receipt()
                    result = None
                    while record["attempts"] < task.max_attempts:
                        move("RUNNING")
                        record["attempts"] += 1
                        self.journal.save(task.task_id, record)
                        execution_started = False
                        try:
                            if await self._bounded(adapter.healthy(), task.timeout_s, cancel) is not True:
                                raise ConnectionError()
                            execution_started = True
                            result = await self._bounded(adapter.execute(deepcopy(task), deepcopy(prepared)),
                                                         task.timeout_s, cancel)
                            if type(result) is not Result:
                                raise ValueError('INVALID_RESULT')
                            break
                        except AdapterError as error:
                            if error.code in AdapterError.BLOCKING:
                                if not execution_started:
                                    record['attempts'] -= 1  # No model execution consumed.
                                move('BLOCKED', error.code)
                                return receipt()
                            if not error.retryable:
                                raise
                            move('RETRYABLE_FAILURE', error.code)
                        except (TimeoutError, ConnectionError, OSError):
                            move("RETRYABLE_FAILURE", "AGENT_UNAVAILABLE")
                    if result is None:
                        return receipt()
                    move("VALIDATING")
                    if type(result) is not Result:
                        raise ValueError("INVALID_RESULT")
                    result = deepcopy(result)
                    result.validate(task, desc)
                    self.registry.connected(agent_id)
                    evidence = getattr(adapter, 'evidence_for', lambda *_: None)(task.task_id, task.correlation_id)
                    if evidence is not None:
                        record['execution_evidence'] = self._execution_evidence(evidence)
                    if (result.confidence < task.min_confidence or result.uncertainties
                            or not result.evidence and not getattr(handler, "allow_empty_evidence", False)):
                        move("WAITING_FOR_REVIEW", "EVIDENCE_REVIEW_REQUIRED")
                        return receipt()
                    handler.validate(task, result, prepared)
                    self.registry.verified(agent_id, task.kind)
                    if cancel is not None and cancel.is_set():
                        move("CANCELLED", "CANCELLED")
                        return receipt()
                    record.update(result_hash=result.digest(), commit_intent=True)
                    move("COMMITTING")

                key = "orchestration-" + fingerprint([task.task_id, digest, task.kind])
                response = await self._bounded(handler.commit(deepcopy(task), deepcopy(result), key),
                                               task.timeout_s, cancel)
                if type(response) is not dict:
                    raise ValueError("INVALID_RESULT")
                record["gateway_receipt_hash"] = fingerprint(response)
                record["artifacts"] = self._artifacts(response)
                move("VERIFYING")
                verified = await self._bounded(handler.verify(deepcopy(task), deepcopy(result), deepcopy(response)),
                                               task.timeout_s, cancel)
                if verified is not True:
                    move("PARTIAL", "READBACK_MISMATCH")
                else:
                    record["verified"] = True
                    record["business_review"] = response.get("requires_review") is True
                    move("WAITING_FOR_REVIEW" if response.get("requires_review") else
                         "PARTIAL" if response.get("partial") else "COMPLETED")
                return receipt()

            except asyncio.CancelledError:
                move("PARTIAL" if record["commit_intent"] else "CANCELLED", "CANCELLED")
                return receipt()
            except Exception as error:
                # Never journal exception text, model content or backend paths.
                allowed = {"UNSUPPORTED_TASK", "PERMISSION_DENIED", "AGENT_UNAVAILABLE",
                           "CAPABILITY_UNAVAILABLE", "EXTERNAL_TRANSFER_DENIED", "INVALID_RESULT",
                           "ADAPTER_VERSION_CONFLICT", "AGENT_DESCRIPTOR_CHANGED", "CANCELLATION_STALLED"}
                code = error.code if isinstance(error, (GatewayError, AdapterError)) else (
                    str(error) if type(error) is ValueError and str(error) in allowed else
                    "TIMEOUT" if isinstance(error, TimeoutError) else "VALIDATION_FAILED")
                # A generic handler can have multiple operations. A later denial
                # cannot prove that an earlier write did not already succeed.
                move("PARTIAL" if record["commit_intent"] else "FAILED", code)
                return receipt()

    @staticmethod
    def _artifacts(value):
        """Only bounded logical IDs and versions survive in task bookkeeping."""
        result = []
        pattern = r"(?:(?:wrong-answer|wrong-analysis|document|asset|source)://sha256/[0-9a-f]{64}|(?:candidate|memory|analysis|history|message):[0-9a-f]{32,64})"
        def visit(item, depth=0):
            if depth > 10 or len(result) >= 128:
                return
            if type(item) is dict:
                entry = {key: val for key, val in item.items()
                         if key in {"source_id", "candidate_id", "memory_id", "analysis_id", "item_id"}
                         and type(val) is str and re.fullmatch(pattern, val)}
                if entry:
                    if type(item.get("version")) is int and item["version"] > 0:
                        entry["version"] = item["version"]
                    result.append(entry)
                for child in item.values():
                    visit(child, depth + 1)
            elif type(item) in (list, tuple):
                for child in item[:128]:
                    visit(child, depth + 1)
        visit(value)
        return result

    async def _bounded(self, awaitable, seconds, cancel):
        if cancel is not None and cancel.is_set():
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.CancelledError()
        operation = asyncio.ensure_future(awaitable)
        cancellation = asyncio.create_task(cancel.wait()) if cancel is not None else None
        try:
            waiting = {operation} | ({cancellation} if cancellation is not None else set())
            done, _ = await asyncio.wait(waiting, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            if cancellation is not None and cancellation in done:
                raise asyncio.CancelledError()
            if operation not in done:
                raise TimeoutError()
            return operation.result()
        finally:
            cleanup = []
            for pending in (operation, cancellation):
                if pending is not None and not pending.done():
                    pending.cancel()
                    cleanup.append(pending)
            if cleanup:
                _, unfinished = await asyncio.wait(cleanup, timeout=.1)
                for pending in unfinished:
                    self.pending_operations.add(pending)
                    pending.add_done_callback(self._observe)
                if unfinished:
                    # In-process code cannot be forcibly killed. Stop retries and
                    # report uncertain writes; adapters needing hard limits use isolation.
                    raise ValueError("CANCELLATION_STALLED")
            for pending in (operation, cancellation):
                if pending is not None and pending.done() and not pending.cancelled():
                    pending.exception()

    def _observe(self, operation):
        self.pending_operations.discard(operation)
        if not operation.cancelled():
            operation.exception()

    @staticmethod
    def _execution_evidence(evidence):
        """Only identifiers and cryptographic digests, never transport payloads."""
        if type(evidence) is not dict:
            raise ValueError('INVALID_RESULT')
        safe = {}
        for key in ('request_sha256', 'response_sha256'):
            value = evidence.get(key)
            if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
                raise ValueError('INVALID_RESULT')
            safe[key] = value
        for key in ('adapter_id', 'adapter_version', 'requested_model', 'provider_reported_model', 'execution_scope'):
            value = evidence.get(key)
            if value is not None:
                if not identifier(value):
                    raise ValueError('INVALID_RESULT')
                safe[key] = value
        return safe
