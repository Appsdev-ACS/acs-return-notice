import os
import requests
import jwt
from flask import Flask, redirect, request, session, url_for,jsonify
from dotenv import load_dotenv
from flask_cors import CORS
from google.oauth2.service_account import Credentials
import os
from google.auth import default
import gspread
from datetime import datetime
from zoneinfo import ZoneInfo


load_dotenv()

app = Flask(__name__)
app.secret_key = "super_secret_key_change_this"
# app.config.update(
#     SESSION_COOKIE_SAMESITE="Lax",
#     SESSION_COOKIE_SECURE=False,
#     SESSION_COOKIE_HTTPONLY=True
# )

app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True
)
# origins = os.getenv("CORS_ORIGINS", "").split(",")

# # CORS(app, supports_credentials=True)
# CORS(app, supports_credentials=True, origins=origins)

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

CORS(
    app,
    supports_credentials=True,
    origins=origins,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

react_base_uri =  os.getenv("REACT_BASE_URI")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
VERACROSS_DOMAIN = os.getenv("VERACROSS_DOMAIN")

SPREADSHEET_NAME="Family Return Data"


AUTH_URL = "https://accounts.veracross.eu/acsad/oauth/authorize"
TOKEN_URL = "https://accounts.veracross.eu/acsad/oauth/token"
VERACROSS_TOKEN_REVOKE_URL = "https://accounts.veracross.com/acsad/oauth/revoke"



sheet_name = "Households"
"""Uploads the DataFrame to Google Sheets."""

# for prod
# creds, _ = default(scopes=[
#     "https://www.googleapis.com/auth/spreadsheets",
#     "https://www.googleapis.com/auth/drive"
# ])


# for local

# SCOPES = [
#     "https://www.googleapis.com/auth/spreadsheets",
#     "https://www.googleapis.com/auth/drive",
# ]

# # Load credentials from JSON file
# creds = Credentials.from_service_account_file(
#     "service-account.json",  # 👈 your file name
#     scopes=SCOPES
# )
# client = gspread.authorize(creds)

# # Open or create the Google Sheet
# try:
#     sheet = client.open(SPREADSHEET_NAME).worksheet(sheet_name)
# except gspread.exceptions.SpreadsheetNotFound:
#     raise Exception("Spreadsheet not found")
# except gspread.exceptions.WorksheetNotFound:
#     raise Exception(f"Worksheet '{sheet_name}' not found")
def get_sheet(worksheet_name):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds, _ = default(scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(worksheet_name)
    


@app.route("/")
def home():
    if "user" in session:
        return f"""
        <h2>Welcome {session['user']['email']}</h2>
        <a href='/logout'>Logout</a>
        """
    return "<a href='/login'>Login with Veracross</a>"

# Step 1: Redirect user to Veracross login
@app.route("/login")
def login():
    # auth_redirect = (
    #     f"{AUTH_URL}?"
    #     f"client_id={CLIENT_ID}&"
    #     f"response_type=code&"
    #     f"redirect_uri={REDIRECT_URI}&"
    #     f"scope=openid%20sso"
    # )
    auth_redirect = f"https://accounts.veracross.eu/acsad/oauth/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&scope=openid%20sso"
    return redirect(auth_redirect)

# Step 2: Veracross redirects back here
@app.route("/oauth2callback")
def callback():
    code = request.args.get("code")

    if not code:
        print("no code")
        return "Authorization failed."

    # Step 3: Exchange code for tokens
    token_response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if token_response.status_code != 200:
        return f"Token exchange failed: {token_response.text}"

    tokens = token_response.json()
    id_token = tokens.get("id_token")

    # Step 4: Decode ID token
    decoded = jwt.decode(id_token, options={"verify_signature": False})

    # Save user session
    session["user"] = decoded

    return redirect(f"{react_base_uri}/#/form")

# @app.route("/logout",  methods=["POST"])
# def logout():
#     session.clear()
#     return {"success": True}

@app.route("/logout", methods=["POST"])
def logout():
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    print("refresh",refresh_token)

    revoke_results = []

    # Try refresh token first, then access token
    for token, hint in [
        (refresh_token, "refresh_token"),
        (access_token, "access_token"),
    ]:
        if token and VERACROSS_TOKEN_REVOKE_URL:
            try:
                print("inside")
                resp = requests.post(
                    VERACROSS_TOKEN_REVOKE_URL,
                    data={
                        "token": token,
                        "token_type_hint": hint,
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                    },
                    timeout=10,
                )
                revoke_results.append({
                    "token_type": hint,
                    "status_code": resp.status_code,
                    "ok": resp.ok,
                    "body": resp.text[:300],
                })
            except Exception as e:
                revoke_results.append({
                    "token_type": hint,
                    "ok": False,
                    "error": str(e),
                })

    session.clear()

    return jsonify({
        "success": True,
        "revocation_attempted": bool(VERACROSS_TOKEN_REVOKE_URL),
        "revocation_results": revoke_results,
    }), 200

@app.route("/api/me")
def me():
    if "user" not in session:
        return {"authenticated": False}, 401

    return {
        "authenticated": True,
        "user": session["user"]
    }



def get_logged_in_email():
    if "user" not in session:
        return None

    user = session["user"]

    # adjust if your token uses a different claim name
    return (
        user.get("email")
        or user.get("upn")
        or user.get("preferred_username")
    )

@app.route("/api/form-data")
def get_form_data():
    user_email = get_logged_in_email()
    if not user_email:
        return {"error": "Unauthorized"}, 401
    
    try:
        sheet = get_sheet(worksheet_name=sheet_name)
        records = sheet.get_all_records()
    except Exception as e:
        return {"error": f"Sheet access failed: {str(e)}"}, 500

    # records = sheet.get_all_records()

    for idx, row in enumerate(records, start=2):
        p1_email = str(row.get("PARENT 1: Email 1", "")).strip().lower()
        p2_email = str(row.get("PARENT 2: Email 1", "")).strip().lower()

        if user_email.strip().lower() in [p1_email, p2_email]:
            parent1 = f'{row.get("Parent 1 Full Name", "")} ({row.get("PARENT 1: Email 1", "")})'.strip()
            parent2 = f'{row.get("Parent 2 Full Name", "")} ({row.get("PARENT 2: Email 1", "")})'.strip()

            child_lines = []

            for i in range(1, 6):
                person_id = row.get(f"Student {i} \nPerson ID", "") or row.get(f"Student {i}\nPerson ID", "")
                full_name = row.get(f"Student {i} \nFull Name", "") or row.get(f"Student {i}\nFull Name", "")
                grade = row.get(f"Student {i} \nCurrent Grade", "") or row.get(f"Student {i}\nCurrent Grade", "")
                homeroom = row.get(f"Student {i}\nHomeroom", "") or row.get(f"Student {i} \nHomeroom", "")

                if full_name or person_id:
                    child_lines.append(
                        # f"ID: {person_id} | Name: {full_name} | Grade: {grade} | Homeroom: {homeroom}"
                        f"{full_name} - {grade}"

                    )

            return jsonify({
                "rowNumber": idx,
                "formData": {
                    "HouseholdId": row.get("Household ID", ""),
                    "HouseholdName": row.get("Household", ""),
                    "PersonId": row.get("PARENT 1: Person ID", "") if user_email.strip().lower() == p1_email else row.get("PARENT 2: Person ID", ""),
                    "Parent_1_Name_and_Email": parent1,
                    "Parent_2_Name_and_Email": parent2.replace("()",""),
                    "Who_is_completing_the_form": row.get("Parent 1 Full Name", "") if user_email.strip().lower() == p1_email else row.get("Parent 2 Full Name", ""),
                    "ChildDetails": "\n".join(child_lines),
                    "DateOfReturn": row.get("Return Date", ""),
                    "comments": row.get("Comments", "")
                }
            })

    return jsonify({
        "rowNumber": None,
        "formData": {
            "HouseholdId": "",
            "HouseholdName": "",
            "PersonId": "",
            "Parent_1_Name_and_Email": "",
            "Parent_2_Name_and_Email": "",
            "Who_is_completing_the_form": "",
            "ChildDetails": "",
            "DateOfReturn": "",
            "comments": ""
        }
    })



@app.route("/api/return-notice", methods=["POST"])
def return_notice():
    user_email = get_logged_in_email()
    if not user_email:
        return {"error": "Unauthorized"}, 401

    data = request.get_json() or {}

    try:
        sheet = get_sheet(worksheet_name=sheet_name)
        records = sheet.get_all_records()
        headers = sheet.row_values(1)
    except Exception as e:
        return {"error": f"Sheet access failed: {str(e)}"}, 500

    row_number = None
    for idx, row in enumerate(records, start=2):
        p1_email = str(row.get("PARENT 1: Email 1", "")).strip().lower()
        p2_email = str(row.get("PARENT 2: Email 1", "")).strip().lower()

        if user_email.strip().lower() in [p1_email, p2_email]:
            row_number = idx
            break

    if not row_number:
        return {"error": "Matching row not found"}, 404

    def col_index(header_name):
        return headers.index(header_name) + 1

    updates = []

    if "Return Date" in headers:
        updates.append({
            "range": gspread.utils.rowcol_to_a1(row_number, col_index("Return Date")),
            "values": [[data.get("DateOfReturn", "")]]
        })

    if "Comments" in headers:
        updates.append({
            "range": gspread.utils.rowcol_to_a1(row_number, col_index("Comments")),
            "values": [[data.get("comments", "")]]
        })

    if "Updated By" in headers:
        updates.append({
            "range": gspread.utils.rowcol_to_a1(row_number, col_index("Updated By")),
            "values": [[data.get("Who_is_completing_the_form", "")]]
        })

    if "Updated On" in headers:
        # timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timestamp = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%Y-%m-%d %H:%M:%S")
        updates.append({
            "range": gspread.utils.rowcol_to_a1(row_number, col_index("Updated On")),
            "values": [[timestamp]]
        })

    if updates:
        sheet.batch_update(updates)

    return {"message": "Form submitted successfully"}



@app.route("/api/location-form-data")
def get_location_form_data():
    user_email = get_logged_in_email()
    if not user_email:
        return {"error": "Unauthorized"}, 401

    try:
        households_sheet = get_sheet(sheet_name)
        household_records = households_sheet.get_all_records()

        location_sheet = get_sheet("Location")
        location_records = location_sheet.get_all_records()
    except Exception as e:
        return {"error": f"Sheet access failed: {str(e)}"}, 500

    for idx, row in enumerate(household_records, start=2):
        p1_email = str(row.get("PARENT 1: Email 1", "")).strip().lower()
        p2_email = str(row.get("PARENT 2: Email 1", "")).strip().lower()
        current_user = user_email.strip().lower()

        if current_user in [p1_email, p2_email]:
            household_id = str(row.get("Household ID", "")).strip()

            parent1 = f'{row.get("Parent 1 Full Name", "")} ({row.get("PARENT 1: Email 1", "")})'.strip()
            parent2 = f'{row.get("Parent 2 Full Name", "")} ({row.get("PARENT 2: Email 1", "")})'.strip().replace("()", "")

            who_is_completing = (
                row.get("Parent 1 Full Name", "")
                if current_user == p1_email
                else row.get("Parent 2 Full Name", "")
            )

            children = []
            for i in range(1, 6):
                person_id = row.get(f"Student {i} \nPerson ID", "") or row.get(f"Student {i}\nPerson ID", "")
                full_name = row.get(f"Student {i} \nFull Name", "") or row.get(f"Student {i}\nFull Name", "")
                grade = row.get(f"Student {i} \nCurrent Grade", "") or row.get(f"Student {i}\nCurrent Grade", "")
                homeroom = row.get(f"Student {i}\nHomeroom", "") or row.get(f"Student {i} \nHomeroom", "")

                if full_name or person_id:
                    children.append({
                        "studentNumber": i,
                        "personId": person_id,
                        "fullName": full_name,
                        "grade": grade,
                        "homeroom": homeroom,
                        "display": f"{person_id} - {full_name} - {grade} - {homeroom}"
                    })

            existing_location = next(
                (
                    r for r in location_records
                    if str(r.get("Household ID", "")).strip() == household_id
                ),
                {}
            )

            return jsonify({
                "rowNumber": idx,
                "formData": {
                    "HouseholdId": household_id,
                    "HouseholdName": row.get("Household", ""),
                    "PersonId": (
                        row.get("PARENT 1: Person ID", "")
                        if current_user == p1_email
                        else row.get("PARENT 2: Person ID", "")
                    ),
                    "Parent_1_Name_and_Email": parent1,
                    "Parent_2_Name_and_Email": parent2,
                    "Who_is_completing_the_form": who_is_completing,
                    "Country": existing_location.get("Country", ""),
                    "City": existing_location.get("City", ""),
                    "Comments": existing_location.get("Comments", ""),
                    "Child1LearningMode": existing_location.get("Child1 Learning Mode", ""),
                    "Child2LearningMode": existing_location.get("Child 2 Learning Mode", ""),
                    "Child3LearningMode": existing_location.get("Child 3 Learning Mode", ""),
                    "Child4LearningMode": existing_location.get("Child 4 Learning Mode", ""),
                    "Child5LearningMode": existing_location.get("Child 5 Learning Mode", ""),
                    "children": children
                }
            })

    return jsonify({
        "rowNumber": None,
        "formData": {
            "HouseholdId": "",
            "HouseholdName": "",
            "PersonId": "",
            "Parent_1_Name_and_Email": "",
            "Parent_2_Name_and_Email": "",
            "Who_is_completing_the_form": "",
            "Country": "",
            "City": "",
            "Comments": "",
            "Child1LearningMode": "",
            "Child2LearningMode": "",
            "Child3LearningMode": "",
            "Child4LearningMode": "",
            "Child5LearningMode": "",
            "children": []
        }
    })

@app.route("/api/location-notice", methods=["POST"])
def location_notice():
    user_email = get_logged_in_email()
    if not user_email:
        return {"error": "Unauthorized"}, 401

    data = request.get_json() or {}

    household_id = str(data.get("HouseholdId", "")).strip()
    if not household_id:
        return {"error": "HouseholdId is required"}, 400

    try:
        sheet = get_sheet(LOCATION_SHEET_NAME)
        records = sheet.get_all_records()
        headers = sheet.row_values(1)
    except Exception as e:
        return {"error": f"Sheet access failed: {str(e)}"}, 500

    timestamp = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%Y-%m-%d %H:%M:%S")

    row_number = None
    for idx, row in enumerate(records, start=2):
        if str(row.get("Household ID", "")).strip() == household_id:
            row_number = idx
            break

    def get_value(header_name):
        mapping = {
            "Household ID": household_id,
            "Completed by": data.get("Who_is_completing_the_form", ""),
            "Timestamp": timestamp,
            "Country": data.get("Country", ""),
            "City": data.get("City", ""),
            "Child1 Learning Mode": data.get("Child1LearningMode", ""),
            "Child 2 Learning Mode": data.get("Child2LearningMode", ""),
            "Child 3 Learning Mode": data.get("Child3LearningMode", ""),
            "Child 4 Learning Mode": data.get("Child4LearningMode", ""),
            "Child 5 Learning Mode": data.get("Child5LearningMode", ""),
            "Comments": data.get("Comments", ""),
        }
        return mapping.get(header_name, "")

    if row_number:
        updates = []
        for header in headers:
            if header in [
                "Household ID",
                "Completed by",
                "Timestamp",
                "Country",
                "City",
                "Child1 Learning Mode",
                "Child 2 Learning Mode",
                "Child 3 Learning Mode",
                "Child 4 Learning Mode",
                "Child 5 Learning Mode",
                "Comments",
            ]:
                col = headers.index(header) + 1
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(row_number, col),
                    "values": [[get_value(header)]]
                })

        if updates:
            sheet.batch_update(updates)

        return {"message": "Location form updated successfully"}

    new_row = [get_value(header) for header in headers]
    sheet.append_row(new_row)

    return {"message": "Location form submitted successfully"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
