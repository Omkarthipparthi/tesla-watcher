import os
import json
import smtplib
import requests
from email.message import EmailMessage

TESLA_ENDPOINT = "https://www.tesla.com/inventory/api/v4/inventory-results"

# Config from Env
MAILERSEND_API_TOKEN = os.environ.get("MAILERSEND_API_TOKEN")
MAILERSEND_DOMAIN = os.environ.get("MAILERSEND_DOMAIN") # e.g. test-ywj2lpnx9ejg7oqz.mlsender.net
MAIL_FROM = os.environ.get("MAIL_FROM") # Verified sender email or from the domain
MAIL_TO = os.environ.get("MAIL_TO")

# Thresholds
MAX_LEASE_PAYMENT = 175  # Filter locally just in case

def build_query():
    # Based on the user's provided decoded query
    return {
        "query": {
            "model": "m3",
            "condition": "used",
            "options": {
                "Year": [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
            },
            "arrangeby": "Price",
            "order": "asc",
            "market": "US",
            "language": "en",
            "super_region": "north america",
            "PaymentType": "lease",
            "paymentRange": "0,200", # Asking for a slightly wider range to be safe, then filtering locally
            "Odometer": "0,97000",
            "lng": -118.1215,
            "lat": 33.7903,
            "zip": "90815",
            "range": 200,
            "region": "CA",
        },
        "offset": 0,
        "count": 50,
        "outsideOffset": 0,
        "outsideSearch": True,
        "isFalconDeliverySelectionEnabled": False,
        "version": None,
    }

def fetch_inventory():
    payload = build_query()
    params = {"query": json.dumps(payload)}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tesla.com/inventory/used/m3",
    }
    
    print("Fetching Tesla inventory...")
    try:
        r = requests.get(TESLA_ENDPOINT, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Error fetching inventory: {e}")
        return None

def find_deals(data):
    if not data or "results" not in data:
        print("No results found in response.")
        return []

    deals = []
    print(f"Analyzing {len(data['results'])} vehicles...")
    
    for car in data["results"]:
        vin = car.get("VIN", "N/A")
        year = car.get("Year", "N/A")
        # Cash price
        price = car.get("Price", car.get("InventoryPrice", 0))
        
        # Lease parsing
        lease_payment = None
        fin_details = car.get("FinplatDetails", {})
        
        # Look for lease calc details
        # Structure example: car['FinplatDetails']['AUTO_LEASE...']['calculated']['outputs']['monthlyPayment']
        for key, finance_data in fin_details.items():
            if "LEASE" in key:
                try:
                    # Try to get monthly payment from calculated outputs
                    calc = finance_data.get("calculated", {})
                    outputs = calc.get("outputs", {})
                    inputs = calc.get("inputs", {})
                    
                    # specific to 'monthlyPayment'
                    val = outputs.get("monthlyPayment") or inputs.get("monthlyPayment")
                    if val:
                        lease_payment = float(val)
                        break 
                except (ValueError, TypeError):
                    continue
        
        # If we didn't find a lease payment in FinplatDetails, wait/skip?
        # The user specifically wants lease < threshold.
        # If we can't find a lease price, we probably shouldn't alert unless we want to be noisy.
        # Let's check our local threshold.
        
        if lease_payment is not None:
            print(f" - {year} M3 (VIN: {vin}): ${lease_payment}/mo (Lease)")
            if lease_payment <= MAX_LEASE_PAYMENT:
                print(f"   >>> MATCH! Price {lease_payment} <= {MAX_LEASE_PAYMENT}")
                deals.append({
                    "vin": vin,
                    "year": year,
                    "price": price,
                    "lease_payment": lease_payment,
                    "link": f"https://www.tesla.com/m3/order/{vin}?titleStatus=USED"
                })
        else:
            # Fallback for debugging - if we wanted to see everything
            # print(f" - {year} M3 (VIN: {vin}): Lease price not found")
            pass

    return deals

def send_notification(deals):
    if not deals:
        print("No deals to notify.")
        return
        
    print(f"Found {len(deals)} MATCHING deals!")
        
    if not MAILERSEND_API_TOKEN or not MAIL_TO:
        print("Missing MailerSend config (MAILERSEND_API_TOKEN, MAIL_TO), skipping email.")
        return

    # Prepare email content
    subject = f"Tesla Alert: {len(deals)} Lease Deal(s) Found!"
    
    html_body = "<h1>Found the following Model 3 Lease deals:</h1><ul>"
    text_body = "Found the following Model 3 Lease deals:\n\n"
    
    for d in deals:
        line = f"Year: {d['year']}, Lease: ${d['lease_payment']}/mo"
        text_body += f"{line}, Link: {d['link']}\n"
        html_body += f"<li>{line} <a href='{d['link']}'>Link</a></li>"
    html_body += "</ul>"

    # MailerSend API
    url = "https://api.mailersend.com/v1/email"
    headers = {
        "Authorization": f"Bearer {MAILERSEND_API_TOKEN}",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    # "From" must match your verified domain/sender in MailerSend
    sender_email = MAIL_FROM or f"notifier@{MAILERSEND_DOMAIN}"
    
    payload = {
        "from": {
            "email": sender_email,
            "name": "Tesla Watcher"
        },
        "to": [
            {
                "email": MAIL_TO,
                "name": "User"
            }
        ],
        "subject": subject,
        "text": text_body,
        "html": html_body
    }

    try:
        print("Sending mail via MailerSend API...")
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code in [200, 202]:
            print("Email sent successfully!")
        else:
            print(f"Failed to send email. Status: {r.status_code}, Response: {r.text}")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    data = fetch_inventory()
    if data:
        # If we successfully got data, filter it
        # Since the API query did the heavy lifting for "lease" and "price range", 
        # we treat all results as candidates. 
        # Ideally we'd filter strictly on `MAX_LEASE_PAYMENT` again here if we could parse it perfectly.
        deals = find_deals(data)
        
        print(f"Found {len(deals)} cars matching query.")
        if deals:
            send_notification(deals)
    else:
        print("No data received.")
