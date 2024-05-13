import requests
from urllib.parse import quote_plus
from flask import Flask, request, jsonify
import os
from lxml import etree
from dotenv import load_dotenv
import yagmail
import logging

app = Flask(__name__)
load_dotenv()

# Robust Configuration Handling
config = {
    'GHL_API_KEY': os.getenv('GHL_API_KEY'),
    'GHL_LOCATION_ID': os.getenv('GHL_LOCATION_ID'),
    'YOUR_GMAIL_ADDRESS': os.getenv('YOUR_GMAIL_ADDRESS'),
    'DRIVECENTRIC_IMPORT_EMAIL': os.getenv('DRIVECENTRIC_IMPORT_EMAIL'),
    'GMAIL_APP_PASSWORD': os.getenv('GMAIL_APP_PASSWORD')
}

missing_config = [key for key, value in config.items() if not value]
if missing_config:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_config)}")

# Logging Setup for Debugging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_ghl_leads():
    """Fetches lead data from GoHighLevel API."""
    encoded_location_id = quote_plus(config['GHL_LOCATION_ID'])
    api_endpoint = f"https://rest.gohighlevel.com/v1/contacts?locationId={encoded_location_id}"
    headers = {"Authorization": f"Bearer {config['GHL_API_KEY']}"}
    
    try:
        response = requests.get(api_endpoint, headers=headers)
        response.raise_for_status()  # Raise exception for bad HTTP status codes
        data = response.json()
        return data.get("contacts", [])  # Handle case where "contacts" key is missing
    except requests.RequestException as e:
        logging.error(f"Error fetching GHL contacts: {e}")
        return []  # Return empty list on error

def generate_adf_xml(leads_data):
    """Generates ADF XML from lead data."""
    if not leads_data:
        logging.warning("No leads found in the API response.")
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

        # Vehicle Information (Trade-in) - Updated to use custom fields
        additional_info = lead.get("Additional Info", {})  # Get data from "Additional Info" card
        vehicle_info = {}
        for key in ["Vehicle Year", "Vehicle Make", "Vehicle Model", "Vehicle Trim", "Vehicle Mileage", "Vehicle Condition"]:
            value = additional_info.get(key, "")  # Extract values from within "Additional Info"
            if value:
                vehicle_info[key] = value


        if vehicle_info.get("Vehicle Year") and vehicle_info.get("Vehicle Make") and vehicle_info.get("Vehicle Model"): 
            vehicle = etree.SubElement(prospect, "vehicle", interest="trade-in")
            for key, value in vehicle_info.items():
                if value:  # Include only fields with values
                    # Map GHL custom field names to DriveCentric XML tags
                    xml_tag = key.replace("Vehicle ", "") 
                    etree.SubElement(vehicle, xml_tag).text = str(value)

        # Tags (Optional)
        tags = lead.get("tags", [])
        for tag in tags:
            etree.SubElement(prospect, "tag").text = tag

        # Source Type Name (New)
        provider_name = lead.get("VENDOR", {}).get("VENDORNAME", "")  
        if provider_name:
            provider = etree.SubElement(prospect, "provider")
            etree.SubElement(provider, "name").text = provider_name

        # Notes (Comments) - Change this section
        ai_memory = lead.get("AI Memory", "")  
        comments = lead.get("COMMENTS", "")  # Get existing comments (if any)
        if ai_memory:
            if comments:
                comments += f"\n\nAI Memory:\n{ai_memory}"  # Append AI Memory
            else:
                comments = f"AI Memory:\n{ai_memory}"  # Use AI Memory as comments if none exist
        if comments:
            etree.SubElement(prospect, "comments").text = comments

    return etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=True)

# Email Sending Function (Refactored)
def send_email(recipient, subject, contents, attachment=None):
    try:
        yag = yagmail.SMTP(config['YOUR_GMAIL_ADDRESS'], config['GMAIL_APP_PASSWORD'])
        yag.send(to=recipient, subject=subject, contents=contents, attachments=attachment)
        logging.info(f"Email sent to {recipient}")
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        


# Webhook Endpoint
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    try:
        lead_data = request.get_json()
        if not lead_data:  
            return jsonify({"error": "Invalid or empty JSON payload"}), 400  

        adf_xml = generate_adf_xml([lead_data])

        if adf_xml:
            with open("lead_export.xml", "wb") as f:
                f.write(adf_xml)

            send_email(
                config['DRIVECENTRIC_IMPORT_EMAIL'], 
                "New Lead from GHL", 
                ["New lead in ADFXML format attached."], 
                "lead_export.xml"
            )

            return jsonify({"message": "Lead processed successfully"}), 200
        else:
            return jsonify({"error": "Error processing lead (no valid ADF XML generated)"}), 400 

    except (ValueError, KeyError, TypeError) as e: 
        logging.error(f"Webhook error: {e}, Payload: {lead_data}")
        return jsonify({"error": "Error processing lead"}), 400  
    except Exception as e: 
        logging.error(f"Unexpected webhook error: {e}, Payload: {lead_data}")
        return jsonify({"error": "Internal Server Error"}), 500
        

if __name__ == "__main__":
    leads = fetch_ghl_leads()
    adf_xml = generate_adf_xml(leads)

    if adf_xml:
        with open("lead_export.xml", "wb") as f:
            f.write(adf_xml)
        print("ADF XML saved to lead_export.xml")

        send_email(
            config['DRIVECENTRIC_IMPORT_EMAIL'],
            "New Leads from GHL",
            ["New leads in ADFXML format attached.", "lead_export.xml"]
        )
    else:
        print("No leads found or all leads have errors.")

    # Start the Flask app
    app.run(debug=True, host='0.0.0.0', port=5001) #Example port
