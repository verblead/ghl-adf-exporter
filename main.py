import requests
from flask import Flask, request
import os
from lxml import etree
from dotenv import load_dotenv
import json
from urllib.parse import quote_plus
import yagmail
from getpass import getpass  # Import for secure password input


# Load environment variables from .env file
load_dotenv()

api_key = os.environ.get('GHL_API_KEY').strip()
location_id = os.environ.get('GHL_LOCATION_ID').strip()
sender_email = os.environ.get('YOUR_GMAIL_ADDRESS').strip()
drive_centric_email = os.environ.get('DRIVECENTRIC_IMPORT_EMAIL').strip()

if not api_key or not location_id or not sender_email or not drive_centric_email:
    raise ValueError("Required environment variables not set or invalid in .env file.")


def fetch_ghl_leads():
    encoded_location_id = quote_plus(location_id)  
    api_endpoint = f"https://rest.gohighlevel.com/v1/contacts?locationId={encoded_location_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    print("Requesting URL:", api_endpoint)

    try:
        response = requests.get(api_endpoint, headers=headers)
        response.raise_for_status()
        data = response.json()
        if "contacts" not in data:
            raise KeyError("No 'contacts' key found in the API response.")
        return data["contacts"]
    except (requests.exceptions.RequestException, KeyError, json.JSONDecodeError) as e:
        print(f"Error fetching or parsing GHL contacts: {e}")
        return []  

def generate_adf_xml(leads_data):
    if not leads_data:
        print("No leads found in the API response.")
        return None

    root = etree.Element("adf")
    for lead in leads_data:
        prospect = etree.SubElement(root, "prospect")
        etree.SubElement(prospect, "id").text = str(lead.get("id", ""))

        customer = etree.SubElement(prospect, "customer")
        contact = etree.SubElement(customer, "contact")

        # Customer Information (Handle Missing Names Gracefully)
        first_name = lead.get("firstName")
        last_name = lead.get("lastName")

        if first_name:
            etree.SubElement(contact, "name", part="first").text = first_name
        if last_name:
            etree.SubElement(contact, "name", part="last").text = last_name

        # Contact Information (Optional)
        for key in ["phone", "email", "address1", "city", "state", "postalCode"]:
            value = lead.get(key, "")
            if value:
                etree.SubElement(contact, key).text = value

        # Vehicle Information (Enhanced)
        vehicle_info = lead.get("vehicleOfInterest", {})
        if vehicle_info:
            vehicle = etree.SubElement(prospect, "vehicle", interest="buy")
            for key in ["year", "make", "model"]:
                value = vehicle_info.get(key, "")
                if value:
                    etree.SubElement(vehicle, key).text = value

        # Tags (Optional)
        tags = lead.get("tags", [])
        for tag in tags:
            etree.SubElement(prospect, "tag").text = tag

        # Lead Source (Optional)
        lead_source = lead.get("source", "")
        if lead_source:
            etree.SubElement(prospect, "leadSource").text = lead_source

        # Notes (Optional)
        notes = lead.get("note", "")
        if notes:
            etree.SubElement(prospect, "notes").text = notes

    return etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    leads = fetch_ghl_leads()
    adf_xml = generate_adf_xml(leads)

    if adf_xml:
        with open("lead_export.xml", "wb") as f:
            f.write(adf_xml)
        print("ADF XML saved to lead_export.xml")

        # Retrieve email credentials (with better error handling)
        sender_email = os.environ.get('YOUR_GMAIL_ADDRESS').strip()
        gmail_app_password = os.environ.get('GMAIL_APP_PASSWORD').strip() 

        if not sender_email:
            raise ValueError("Sender email not found in .env file.")
        if not gmail_app_password:  # Check only for the app password
            raise ValueError("Gmail app password not found in .env file.")

        try:
            yag = yagmail.SMTP(sender_email, gmail_app_password)
            yag.send(
                to=drive_centric_email,
                subject="New Leads from GHL",
                contents=["New leads in ADFXML format attached.", "lead_export.xml"]
            )
            print(f"Email sent to {drive_centric_email}")
        except Exception as e:
            print(f"Error sending email: {e}")

    else:
        print("No leads found or all leads have errors.") 

        app = Flask(__name__)

@app.route('/webhook', methods=['POST']) 
def handle_webhook():
    try:
        lead_data = request.get_json()
        adf_xml = generate_adf_xml([lead_data])  # Wrap in a list as generate_adf_xml expects a list

        if adf_xml:
            with open("lead_export.xml", "wb") as f:
                f.write(adf_xml)
            print("ADF XML saved to lead_export.xml")

            # ... (your email sending code using yagmail or other library)
            
            return "Lead processed successfully", 200
        else:
            return "Error processing lead", 400

    except Exception as e:
        print(f"Error processing webhook: {e}")
        return "Error", 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) #Example port