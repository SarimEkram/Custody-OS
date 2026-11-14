"""Tool catalogue (docs/ARCHITECTURE.md "Tool catalogue").

Every tool carries what CLAUDE.md requires: a reversible tag (None = read-only or a
recovery action), its compensation or recovery step, and the scan it should produce.
Compensations and recovery actions also go through execute() (recovery policy) but are
hidden from the LLM: only the recovery planner calls them.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    system: str  # freezer | scheduler | courier | station | internal
    params: dict[str, str] = field(default_factory=dict)
    reversible: bool | None = None
    compensation: str | None = None
    recovery: str | None = None
    expected_scan: str | None = None
    llm: bool = True


_TOOLS = [
    Tool(
        "parse_request",
        "Record the sample and route understood from the request.",
        "internal",
        {
            "sample_id": "Sample ID",
            "from": "Source location id",
            "to": "Destination location id",
            "window": "Requested time window",
        },
    ),
    Tool(
        "request_confirmation",
        "Read the plan back to the tech and wait for confirmation.",
        "internal",
        {"summary": "Plain-English plan to read back"},
    ),
    Tool("lookup_sample", "Find where a sample is stored.", "freezer", {"sample_id": "Sample ID"}),
    Tool(
        "reserve_station",
        "Reserve the destination station.",
        "scheduler",
        {"station_id": "Station location id", "window": "Time window"},
        reversible=True,
        compensation="cancel_reservation",
    ),
    Tool(
        "request_courier",
        "Book a courier from the source to the destination.",
        "courier",
        {"from": "Source location id", "to": "Destination location id", "window": "Time window"},
        reversible=True,
        compensation="cancel_courier_job",
    ),
    Tool(
        "notify",
        "Send a message to a person.",
        "internal",
        {"user_id": "Recipient id", "message": "Message text"},
        reversible=True,
        compensation="notify",
    ),  # compensation is a retraction notice
    Tool(
        "checkout_sample",
        "Take the sample out of its freezer. Cannot be undone.",
        "freezer",
        {"sample_id": "Sample ID"},
        reversible=False,
        expected_scan="checkout",
        recovery="return_to_storage: nearest certified freezer before the clock runs out",
    ),
    Tool(
        "handoff_to_courier",
        "Hand the sample to the courier. Cannot be undone.",
        "courier",
        {"transfer_id": "Transfer id"},
        reversible=False,
        expected_scan="handoff",
        recovery="reroute the courier to the nearest certified freezer",
    ),
    Tool(
        "deliver",
        "Deliver the sample to the destination station. Cannot be undone.",
        "station",
        {"transfer_id": "Transfer id"},
        reversible=False,
        expected_scan="intake",
        recovery="reroute the courier to the nearest certified freezer",
    ),
    # Compensations and recovery actions: run by the recovery planner, never offered to the LLM.
    Tool(
        "cancel_reservation",
        "Cancel a station reservation.",
        "scheduler",
        {"reservation_id": "Reservation id"},
        llm=False,
    ),
    Tool(
        "cancel_courier_job",
        "Cancel a courier job that has not been picked up.",
        "courier",
        {"job_id": "Courier job id"},
        llm=False,
    ),
    Tool(
        "return_to_storage",
        "Send a sample back to a freezer.",
        "courier",
        {"sample_id": "Sample ID", "freezer_id": "Freezer location id"},
        expected_scan="checkin",
        llm=False,
    ),
]

TOOLS: dict[str, Tool] = {t.name: t for t in _TOOLS}


def irreversible_tools() -> set[str]:
    return {t.name for t in _TOOLS if t.reversible is False}


def openai_tools() -> list[dict]:
    """LLM-visible tools in OpenAI function format; `reason` is always required."""
    out = []
    for t in _TOOLS:
        if not t.llm:
            continue
        props = {k: {"type": "string", "description": v} for k, v in t.params.items()}
        props["reason"] = {"type": "string", "description": "Why this call is being made"}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {"type": "object", "properties": props, "required": list(props)},
                },
            }
        )
    return out
