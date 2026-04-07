# AgentCore Streaming Protocol

## Table of Contents
1. [Invocation API](#invocation-api)
2. [Streaming Event Format](#streaming-event-format)
3. [Interrupt Protocol](#interrupt-protocol)
4. [Session ID Requirements](#session-id-requirements)

---

## Invocation API

Callers invoke the AgentCore Runtime via its REST API. The URL is constructed from the Runtime ARN:

```
POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{url-encoded-arn}/invocations?qualifier=DEFAULT
```

### Required Headers

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <JWT>` — Cognito token, validated by Runtime's CUSTOM_JWT authorizer |
| `Content-Type` | `application/json` |
| `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` | Session identifier (min 33 chars) |

### Request Payloads

**Normal invocation:**
```json
{"prompt": "user message"}
```

**Resume from interrupt:**
```json
{
    "interrupt_responses": [{
        "interruptResponse": {
            "interruptId": "session-123-confirmation",
            "response": "yes"
        }
    }]
}
```

When resuming, the `prompt` field is omitted — only `interrupt_responses` is sent.

---

## Streaming Event Format

AgentCore streams responses as Server-Sent Events with nested JSON payloads.

### Event Sequence (Normal Turn)

```
data: {"event": {"messageStart": {"role": "assistant"}}}
data: {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
data: {"event": {"contentBlockDelta": {"delta": {"text": " there!"}}}}
data: {"event": {"contentBlockStop": {}}}
data: {"event": {"messageStop": {"stopReason": "end_turn"}}}
```

### Event Sequence (Interrupted for HITL)

```
data: {"event": {"messageStart": {"role": "assistant"}}}
data: {"event": {"contentBlockDelta": {"delta": {"text": "I can process..."}}}}
data: {"type": "confirmation_request", "interrupt_id": "...", "message": "Confirm?", ...}
data: {"event": {"messageStop": {"stopReason": "interrupt"}}}
```

### Key Events

| Event | Meaning |
|-------|---------|
| `messageStart` | New assistant turn beginning |
| `contentBlockDelta` | Text chunk — extract via `event.contentBlockDelta.delta.text` |
| `contentBlockStop` | Current content block finished |
| `messageStop` with `stopReason: "end_turn"` | Normal completion |
| `messageStop` with `stopReason: "interrupt"` | Agent paused for HITL — do not treat as complete |
| Custom event with `"type"` field | Application-level event (e.g., `confirmation_request` from HITL hook) |

---

## Interrupt Protocol

When the agent hits a HITL checkpoint (via `event.interrupt()` in a Strands hook), the streaming protocol changes:

### Flow

```
1. Agent decides to call a tool requiring confirmation
2. ConfirmationHook fires → event.interrupt() pauses the agent
3. Stream emits confirmation_request event (custom, from the hook)
4. Stream emits messageStop with stopReason: "interrupt"
5. Stream ends — caller must NOT treat this as completion

6. Caller shows confirmation UI to user
7. User responds (yes/no)
8. Caller sends new POST with interrupt_responses payload
9. Agent resumes — hook receives response, tool executes or is cancelled
10. New stream begins with the result
```

### Confirmation Event Payload

The `confirmation_request` event is application-defined (sent by the hook via `event.interrupt()`, not by AgentCore itself). The structure is entirely up to your hook — AgentCore just passes it through. A typical shape:

```json
{
    "type": "confirmation_request",
    "interrupt_id": "<unique-id>",
    "message": "<human-readable description of what needs confirmation>",
    "data": { ... },
    "options": [
        {"label": "Confirm", "value": "yes"},
        {"label": "Cancel", "value": "no"}
    ]
}
```

The `interrupt_id` is what the caller sends back in the `interrupt_responses` payload to resume. Everything else (`message`, `data`, `options`) is for the frontend to render a confirmation UI — design it to fit your domain.

### Key Rule

**`stopReason: "interrupt"` means the conversation is not finished.** Do not send a completion signal to the frontend. The caller must collect the user's response and resume the agent with `interrupt_responses`.

---

## Session ID Requirements

- **Minimum 33 characters** — AgentCore rejects shorter IDs
- **Same ID for entire conversation** — including interrupt resumptions
- **Routes to the same container** — same session ID = same VM = same agent instance with accumulated message history
- **Pad if needed**: `f"{session_id}-{'0' * (33 - len(session_id))}"`
