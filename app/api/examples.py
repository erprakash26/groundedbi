"""Example questions, defined once.

Used in two places so they can't drift apart: the /ask examples dropdown in /docs and
GET /ask/examples, which the website shows as "Or try one of these".
"""

from fastapi.openapi.models import Example

from app.schemas.ask import AskExample

ASK_EXAMPLES: tuple[AskExample, ...] = (
    AskExample(summary="What drove a drop", question="Why did revenue drop in March compared with February?"),
    AskExample(summary="Unknown region (asks back)", question="Revenue in Europe in March"),
    AskExample(summary="Metric not in the data", question="What was our profit in March?"),
    AskExample(summary="Wrong premise (corrected)", question="Why did revenue drop from May to June?"),
)

# The keys are only the dropdown's internal ids in /docs.
OPENAPI_EXAMPLES: dict[str, Example] = {
    f"example_{number}": Example(summary=example.summary, value={"question": example.question})
    for number, example in enumerate(ASK_EXAMPLES, start=1)
}
