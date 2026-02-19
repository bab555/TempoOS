# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Event Type Constants — The vocabulary of TempoOS.

All event types MUST be UPPERCASE strings.
"""

# --- Commands (Kernel → Worker/Node) ---
CMD_EXECUTE = "CMD_EXECUTE"

# --- Results (Worker/Node → Kernel) ---
EVENT_RESULT = "EVENT_RESULT"
EVENT_ERROR = "EVENT_ERROR"

# --- State Transitions ---
STATE_TRANSITION = "STATE_TRANSITION"
STEP_DONE = "STEP_DONE"
NEED_USER_INPUT = "NEED_USER_INPUT"

# --- User Actions ---
USER_CONFIRM = "USER_CONFIRM"
USER_SKIP = "USER_SKIP"
USER_MODIFY = "USER_MODIFY"
USER_ROLLBACK = "USER_ROLLBACK"

# --- Session Lifecycle ---
SESSION_START = "SESSION_START"
SESSION_PAUSE = "SESSION_PAUSE"
SESSION_RESUME = "SESSION_RESUME"
SESSION_ABORT = "SESSION_ABORT"
SESSION_COMPLETE = "SESSION_COMPLETE"

# --- File Processing ---
FILE_UPLOADED = "FILE_UPLOADED"   # Agent → Bus: file uploaded to OSS, needs processing
FILE_READY = "FILE_READY"        # Tonglu → Bus: file parsed, text content available

# --- System ---
HEARTBEAT = "HEARTBEAT"
ABORT = "ABORT"

# All known event types (for validation)
ALL_EVENT_TYPES = {
    CMD_EXECUTE,
    EVENT_RESULT,
    EVENT_ERROR,
    STATE_TRANSITION,
    STEP_DONE,
    NEED_USER_INPUT,
    USER_CONFIRM,
    USER_SKIP,
    USER_MODIFY,
    USER_ROLLBACK,
    SESSION_START,
    SESSION_PAUSE,
    SESSION_RESUME,
    SESSION_ABORT,
    SESSION_COMPLETE,
    FILE_UPLOADED,
    FILE_READY,
    HEARTBEAT,
    ABORT,
}
