import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..coach import CoachBusy, ask_coach
from ..database import get_db
from ..deps import get_user
from ..models import User
from ..schemas import CoachReply, CoachRequest

router = APIRouter(prefix="/users/{user_id}/coach", tags=["coach"])


def get_ai_client():
    """Dependency that provides the Claude (Anthropic) client.

    Being a dependency means tests can swap in a fake client, so the test suite
    never calls the real API (no cost, no network, predictable answers).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="The coach needs an API key. Set ANTHROPIC_API_KEY and restart the server.",
        )
    import anthropic  # imported here so the rest of the app runs without it
    return anthropic.Anthropic(api_key=api_key)


@router.post("", response_model=CoachReply)
def chat(
    payload: CoachRequest,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
    client=Depends(get_ai_client),
):
    history = [turn.model_dump() for turn in payload.history]
    try:
        reply, actions = ask_coach(db, user, payload.message, history, client, payload.date)
    except CoachBusy:
        raise HTTPException(
            status_code=503,
            detail="The AI is overloaded right now (Anthropic's side). Please try again in a minute.",
        )
    except Exception as e:  # network trouble, invalid key, rate limit...
        raise HTTPException(status_code=502, detail=f"Coach unavailable: {type(e).__name__}: {e}")
    return CoachReply(reply=reply, actions=actions)
