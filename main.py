import requests
from urllib.parse import quote_plus
from flask import Flask, request, jsonify
import os
from lxml import etree
from dotenv import load_dotenv
import yagmail
import logging
import atexit  # Import the atexit module
from threading import Thread, Event
import time
import signal

# Global Flask app object
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

        # Vehicle Information (Enhanced)
        vehicle_info = lead.get("vehicleOfInterest", {})
        if vehicle_info:
            vehicle = etree.SubElement(prospect, "vehicle", interest="buy")
            for key in ["year", "make", "model"]:
                value = vehicle_info.get(key, "")
                if value:
                    etree.SubElement(vehicle, key).text = value    

        # ID with Source
        source = "VERBLEAD"  # Hardcoded as VERBLEAD per your requirement
        etree.SubElement(prospect, "id", sequence="1", source=source).text = str(lead.get("id", ""))


        # Comments (Including AI Memory from ChatGPT)
        comments = lead.get("CUSTOMER", {}).get("COMMENTS", "")
        ai_memory = lead.get("Chat GPT", "")  # Assuming AI Memory is under "Chat GPT"
        if ai_memory:
            comments = f"{comments}\n\nAI Memory:\n{ai_memory}"
        if comments:
            etree.SubElement(customer, "comments").text = comments

        # Vendor Information 
        vendor_name = lead.get("VENDOR", {}).get("VENDORNAME", "")
        if vendor_name:
            vendor = etree.SubElement(prospect, "vendor")
            etree.SubElement(vendor, "vendorname").text = vendor_name

        # Provider Information (Hardcoded as VERBLEAD)
        provider = etree.SubElement(prospect, "provider")
        etree.SubElement(provider, "name", part="full").text = "VERBLEAD"
        etree.SubElement(provider, "service").text = "AI Sales"


        # Tags (Optional)
        tags = lead.get("tags", [])
        for tag in tags:
            etree.SubElement(prospect, "tag").text = tag


    return etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=True)

# Email Sending Function (Refactored)
def send_email(recipient, subject, contents, attachment=None):
    try:
        yag = yagmail.SMTP(config['YOUR_GMAIL_ADDRESS'], config['GMAIL_APP_PASSWORD'])
        yag.send(to=recipient, subject=subject, contents=contents, attachments=attachment)
        logging.info(f"Email sent to {recipient}")
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        

# Global variable to store processed lead IDs (consider using a persistent storage like a database in production)
processed_leads = set()
shutdown_event = Event()  # Event to signal server shutdown

# Webhook Endpoint
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    try:
        lead_data = request.get_json()
        if not lead_data:
            return jsonify({"error": "Invalid or empty JSON payload"}), 400

        lead_id = lead_data.get("id")

        # Check for duplicate lead
        if lead_id in processed_leads:
            logging.warning(f"Duplicate lead detected: {lead_id}")
            return jsonify({"message": "Duplicate lead, ignoring"}), 200
        else:
            processed_leads.add(lead_id)

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
    finally:
        # Now, shut down the Flask app explicitly (after handling the request)
        shutdown_func = request.environ.get('werkzeug.server.shutdown')
        if shutdown_func:
            shutdown_func()
        logging.info("Flask app shutting down...")    

# Function to shutdown the Flask app
def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()

@app.route('/shutdown')
def shutdown():
    shutdown_server()
    return 'Server shutting down...'


if __name__ == "__main__":

    def raise_keyboard_interrupt():
        raise KeyboardInterrupt
    
    def wait_and_shutdown():
        time.sleep(15)
        raise_keyboard_interrupt()

    # Process initial leads (run only once)
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

    # Now, start the Flask app after initial processing or error handling
    Thread(target=app.run, kwargs={'debug': False, 'host': '0.0.0.0', 'port': 5000, 'use_reloader': False}).start() 

    # START THE SHUTDOWN TIMER
    # Set up a signal handler to catch SIGALRM (the alarm signal)
    signal.signal(signal.SIGALRM, raise_keyboard_interrupt)

    # Set an alarm to go off in 15 seconds
    signal.alarm(15)

    try:
        # wait for 15 seconds or until shutdown is requested
        wait_and_shutdown()
    except KeyboardInterrupt:
        logging.info("Shutdown signal received")
        shutdown_server() # shutdown server
        exit(0)
