# ================================================================
# UPGRADED HR SYSTEM — ENTERPRISE VERSION (v2)
# ================================================================
from fastapi import APIRouter, HTTPException
import sqlite3, json, os, datetime

HR = APIRouter()
DB_NAME = "users.db"


# ================================================================
# Utility — Read DB
# ================================================================
def fetch(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def execute(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    conn.close()


# ================================================================
# FILTER + SEARCH + SORT ENGINE
# ================================================================
def filter_sort_candidates(candidates, search, role, min_score, status):

    # SEARCH
    if search:
        search = search.lower()
        candidates = [
            c for c in candidates
            if search in c["name"].lower()
            or search in c["email"].lower()
            or search in (c["phone"] or "").lower()
        ]

    # ROLE FILTER
    if role and role.lower() != "all":
        candidates = [c for c in candidates if c["role"].lower() == role.lower()]

    # SCORE FILTER
    if min_score:
        candidates = [c for c in candidates if c["overall_score"] >= min_score]

    # STATUS FILTER
    if status and status != "all":
        candidates = [c for c in candidates if c["status"].lower() == status.lower()]

    # SORT BY OVERALL SCORE DESC
    candidates.sort(key=lambda x: x["overall_score"], reverse=True)

    return candidates


# ================================================================
# GET FULL CANDIDATE LIST WITH SEARCH + FILTER + SORT
# ================================================================
@HR.get("/hr/candidates")
def get_candidates(
    search: str = None,
    role: str = None,
    min_score: int = None,
    status: str = None
):

    rows = fetch("""
        SELECT email, username, phone, role
        FROM users
        WHERE role='candidate'
    """)

    candidates = []

    for email, username, phone, role in rows:

        # Resume Score
        resume_row = fetch(
            "SELECT resume_score FROM resumes WHERE user_email=?",
            (email,)
        )
        resume_score = resume_row[0][0] if resume_row else 0

        # Mock Score
        mock_row = fetch(
            "SELECT MAX(score) FROM mock_reports WHERE user_email=?",
            (email,)
        )
        mock_score = mock_row[0][0] if mock_row and mock_row[0][0] else 0

        # Interview Average
        interview_rows = fetch(
            "SELECT score FROM interviews WHERE user_email=?",
            (email,)
        )
        interview_scores = [r[0] for r in interview_rows]
        interview_avg = int(sum(interview_scores)/len(interview_scores)) if interview_scores else 0

        # Overall Score
        overall_score = int((interview_avg * 0.6) + (mock_score * 0.2) + (resume_score * 0.2))

        # Candidate Status
        status_row = fetch(
            "SELECT status FROM hr_status WHERE email=?",
            (email,)
        )
        cand_status = status_row[0][0] if status_row else "new"

        candidates.append({
            "email": email,
            "name": username,
            "phone": phone,
            "role": role,
            "resume_score": resume_score,
            "mock_score": mock_score,
            "interview_score": interview_avg,
            "overall_score": overall_score,
            "status": cand_status,
            "profile_pic": f"https://api.multiavatar.com/{username}.png"
        })

    return {
        "candidates": filter_sort_candidates(candidates, search, role, min_score, status)
    }


# ================================================================
# FULL CANDIDATE PROFILE (TIMELINE ENABLED)
# ================================================================
@HR.get("/hr/candidate/{email}")
def candidate_profile(email: str):

    # USER DETAILS
    row = fetch("""
        SELECT username, phone, role
        FROM users WHERE email=?
    """, (email,))

    if not row:
        raise HTTPException(404, "Candidate not found")

    username, phone, role = row[0]

    # Resume Data
    resume_data = fetch("""
        SELECT resume_score, resume_url
        FROM resumes WHERE user_email=?
    """, (email,))

    resume_score = resume_data[0][0] if resume_data else 0
    resume_url = resume_data[0][1] if resume_data else None

    # Interview Reports
    interview_reports = fetch("""
        SELECT session_json, score, tips, date
        FROM interviews WHERE user_email=?
        ORDER BY date DESC
    """, (email,))

    interview_data = []
    for s, score, tips, date in interview_reports:
        try: parsed = json.loads(s)
        except: parsed = s

        interview_data.append({
            "date": date,
            "score": score,
            "summary": tips,
            "session": parsed
        })

    # Mock Reports
    mock_reports = fetch("""
        SELECT report_json, date
        FROM mock_reports WHERE user_email=?
        ORDER BY date DESC
    """, (email,))

    mock_data = []
    for r, date in mock_reports:
        try: parsed = json.loads(r)
        except: parsed = r
        mock_data.append({
            "date": date,
            "report": parsed
        })

    # Notes
    notes = fetch("SELECT note, date FROM hr_notes WHERE email=? ORDER BY date DESC", (email,))

    note_list = [{"note": n, "date": d} for n, d in notes]

    # Status
    status_row = fetch("SELECT status FROM hr_status WHERE email=?", (email,))
    status = status_row[0][0] if status_row else "new"

    # Timeline
    timeline = []
    if resume_url: timeline.append("Resume Uploaded")
    if mock_data: timeline.append("Mock Interview Completed")
    if interview_data: timeline.append("AI Interview Completed")
    timeline.append(f"Current Status: {status.title()}")

    # ------------------------------------------
    # BUILD FINAL RESPONSE
    # ------------------------------------------
    return {
        "name": username,
        "email": email,
        "phone": phone,
        "role": role,
        "profile_pic": f"https://api.multiavatar.com/{username}.png",

        # Resume
        "resume_score": resume_score,
        "resume_url": resume_url,

        # Reports
        "interview_reports": interview_data,
        "mock_reports": mock_data,

        # Notes
        "notes": note_list,

        # Status
        "status": status,

        # Timeline
        "timeline": timeline
    }


# ================================================================
# UPDATE CANDIDATE STATUS (shortlist/reject/hired)
# ================================================================
@HR.post("/hr/candidate/{email}/status/{state}")
def update_status(email: str, state: str):

    valid_states = ["new", "shortlisted", "rejected", "final", "hired"]

    if state not in valid_states:
        raise HTTPException(400, "Invalid status")

    execute("""
        INSERT OR REPLACE INTO hr_status(email, status)
        VALUES(?, ?)
    """, (email, state))

    return {"message": "Status updated", "status": state}


# ================================================================
# ADD HR NOTES
# ================================================================
@HR.post("/hr/candidate/{email}/add-note")
def add_note(email: str, note: str):
    execute("""
        INSERT INTO hr_notes(email, note, date)
        VALUES(?, ?, ?)
    """, (email, note, datetime.datetime.utcnow().isoformat()))

    return {"message": "Note added"}
