from __future__ import annotations

from html import escape
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from parking_bot import admin_agent, dynamic_db


app = FastAPI(title="Parking Bot Admin Server")


class AdminDecision(BaseModel):
	reservation_id: int
	decision: str
	note: Optional[str] = None


@app.get("/admin/pending")
def pending_reservations() -> JSONResponse:
	return JSONResponse(content=dynamic_db.list_pending_reservations())


@app.get("/admin/", response_class=HTMLResponse)
def admin_home() -> str:
	rows = dynamic_db.list_pending_reservations()
	headers = _headers(rows)
	body = []
	for row in rows:
		rid = _reservation_id(row)
		cells = "".join(f"<td>{escape(str(value))}</td>" for value in _values(row))
		body.append(
			"<tr>"
			f"{cells}"
			"<td>"
			f"<form method='post' action='/admin/decision' style='display:inline'>"
			f"<input type='hidden' name='reservation_id' value='{escape(str(rid))}'>"
			"<input type='hidden' name='decision' value='approve'>"
			"<button type='submit'>Approve</button>"
			"</form> "
			f"<form method='post' action='/admin/decision' style='display:inline'>"
			f"<input type='hidden' name='reservation_id' value='{escape(str(rid))}'>"
			"<input type='hidden' name='decision' value='reject'>"
			"<button type='submit'>Reject</button>"
			"</form>"
			"</td>"
			"</tr>"
		)

	html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>Parking Bot Admin</title>
  <style>
	body {{ font-family: Arial, sans-serif; margin: 24px; }}
	table {{ border-collapse: collapse; width: 100%; }}
	th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
	th {{ background: #f5f5f5; }}
	button {{ margin-right: 8px; }}
  </style>
</head>
<body>
  <h1>Pending Reservations</h1>
  <table>
	<thead>
	  <tr>
		{''.join(f'<th>{escape(str(h))}</th>' for h in headers)}
		<th>Actions</th>
	  </tr>
	</thead>
	<tbody>
	  {''.join(body) if body else "<tr><td colspan='99'>No pending reservations</td></tr>"}
	</tbody>
  </table>
</body>
</html>"""
	return html


def _run_decision(reservation_id: int, decision: str, note: Optional[str]) -> str:
	try:
		return admin_agent.handle_admin_decision(
			reservation_id=reservation_id,
			decision=decision,
			note=note or "",
		)
	except Exception as exc:
		raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/admin/decision")
def admin_decision_form(
	reservation_id: int = Form(...),
	decision: str = Form(...),
	note: Optional[str] = Form(None),
) -> RedirectResponse:
	"""Called by the admin HTML page (form-encoded). Applies the decision and
	redirects back to the pending list (Post/Redirect/Get)."""
	_run_decision(reservation_id, decision, note)
	return RedirectResponse(url="/admin/", status_code=303)


@app.post("/admin/api/decision")
def admin_decision_json(payload: AdminDecision) -> JSONResponse:
	"""JSON API for programmatic clients / tests."""
	outcome = _run_decision(payload.reservation_id, payload.decision, payload.note)
	return JSONResponse(content={"message": outcome})


def _headers(rows: list[Any]) -> list[str]:
	for row in rows:
		if isinstance(row, dict):
			return list(row.keys())
	return ["reservation_id"]


def _values(row: Any) -> list[Any]:
	if isinstance(row, dict):
		return list(row.values())
	if isinstance(row, (list, tuple)):
		return list(row)
	return [row]


def _reservation_id(row: Any) -> Any:
	if isinstance(row, dict):
		return row.get("id", row.get("reservation_id", ""))
	if isinstance(row, (list, tuple)) and row:
		return row[0]
	return row
