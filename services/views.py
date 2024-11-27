from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FileUploadParser, MultiPartParser
from rest_framework.response import Response
from rest_framework import status
from .utils.mongo import get_mongo_db
import bcrypt
from django.http import HttpResponse
import base64
import requests
from django.conf import settings
import random
import csv
from io import StringIO
import concurrent
from datetime import datetime
import uuid
import os

def calculate_randomized_cost(base_cost, adjustment_range=10):
    adjustment = random.uniform(-adjustment_range, adjustment_range)
    adjusted_cost = max(0, base_cost + adjustment)
    return round(adjusted_cost, 2)

def calculate_randomized_days():
    adjustment = random.uniform(5, 8)  # Random float between 5 and 8
    return int(round(adjustment))  # Convert the rounded value to an integer


@api_view(['POST'])
def registration(request):
    db = get_mongo_db()
    collection = db["users"]
    email = request.data.get('email')
    password = request.data.get('password')
    if not email or not password:
        return Response({"error": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

    if collection.find_one({"email": email}):
        return Response({"error": "Email already exists."}, status=status.HTTP_400_BAD_REQUEST)
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

    user_data = {"email": email, "password": hashed_password}
    result = collection.insert_one(user_data)

    return Response({"Message": "Registration successful", "User ID": str(result.inserted_id)}, status=status.HTTP_201_CREATED)

@api_view(['POST'])
def login(request):
    db = get_mongo_db()
    collection = db["users"]
    
    email = request.data.get('email')
    password = request.data.get('password')

    if not email or not password:
        return Response({"error": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)
    user = collection.find_one({"email": email})

    if not user:
        return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
    if bcrypt.checkpw(password.encode('utf-8'), user['password']):
        client_id = settings.UPS_CLIENT_ID
        client_secret = settings.UPS_CLIENT_SECRET
        credentials = f"{client_id}:{client_secret}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"
        }
        payload = {"grant_type": "client_credentials"}
        response = requests.post("https://onlinetools.ups.com/security/v1/oauth/token", headers=headers, data=payload)
        if response.status_code == 200:
            access_token = response.json().get('access_token')
        return Response({"Message": "Login successful", "User ID": str(user['_id']), "token": access_token}, status=status.HTTP_200_OK)
    else:
        return Response({"error": "Invalid password."}, status=status.HTTP_401_UNAUTHORIZED)
    
@api_view(['POST'])
def validate_address(request):
    data = request.data
    # Check if access token is present
    access_token = data.get("access_token")
    if not access_token:
        return Response({"error": "Access token is missing. Please log in again."}, status=status.HTTP_401_UNAUTHORIZED)

    # Extract other required fields from the request
    name = data.get("name")
    address = data.get("addr")
    city = data.get("city")
    zip_code = data.get("zip")

    # Ensure all required fields are present in the request data
    if not all([name, address, city, zip_code]):
        return Response({"error": "All fields (name, addr, city, zip) are required."}, status=status.HTTP_400_BAD_REQUEST)

    # Prepare the payload for the UPS API
    payload = {
        "XAVRequest": {
            "AddressKeyFormat": {
                "ConsigneeName": name,
                "AddressLine": address,
                "PoliticalDivision2": city,
                "PostcodePrimaryLow": zip_code,
                "CountryCode": "US"
            }
        }
    }

    # Define headers including the access token for authorization
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    # Make the POST request to the UPS API
    try:
        response = requests.post("https://onlinetools.ups.com/api/addressvalidation/v2/1", headers=headers, json=payload)
        response.raise_for_status()  # Raise an HTTPError if the response code indicates failure
    except requests.exceptions.HTTPError as http_err:
        return Response({"error": "Address validation failed.", "details": str(http_err)}, status=response.status_code)
    except requests.exceptions.RequestException as req_err:
        return Response({"error": "An error occurred while validating the address.", "details": str(req_err)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Process the response if successful
    if response.status_code == 200:
        try:
            data = response.json()

            # Check if 'Candidate' list is empty before accessing its first element
            candidates = data.get('XAVResponse', {}).get('Candidate', [])
            if not candidates:
                return Response({"error": "Not Valid Address."}, status=status.HTTP_404_NOT_FOUND)

            address_data = candidates[0].get("AddressKeyFormat", {})

            # Return the response data
            return Response({"response_code": data.get('XAVResponse', {}).get('Response', {}).get('ResponseStatus', {}).get('Code'),
                             "data": address_data})

        except ValueError:
            return Response({"error": "Invalid response format from UPS API."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        return Response({"error": "Address validation failed.", "details": response.text}, status=response.status_code)


# Function to calculate UPS shipping cost
def ups_shipping(access_token, address_data):
    url = "https://onlinetools.ups.com/api/shipments/2/ship"
    
    # Check if necessary address data exists
    if not all([address_data.get('sender'), address_data.get('receiver')]):
        return {"error": "Missing sender or receiver data."}

    payload = {
        "ShipmentRequest": {
            "Request": {
                "SubVersion": "1801",
                "RequestOption": "nonvalidate",
                "TransactionReference": {"CustomerContext": ""}
            },
            "Shipment": {
                "Description": "",
                "Shipper": {
                    "Name": address_data['sender'].get('name'),
                    "Phone": {"Number": address_data['sender'].get('phone'), "Extension": " "},
                    "ShipperNumber": "C870V5",
                    "Address": {
                        "AddressLine": [address_data['sender'].get('addr')],
                        "City": address_data['sender'].get('city'),
                        "StateProvinceCode": address_data['sender'].get('state'),
                        "PostalCode": address_data['sender'].get('zip'),
                        "CountryCode": "US"
                    }
                },
                "ShipTo": {
                    "Name": address_data['receiver'].get('name'),
                    "Phone": {"Number": address_data['receiver'].get('phone')},
                    "Address": {
                        "AddressLine": [address_data['receiver'].get('addr')],
                        "City": address_data['receiver'].get('city'),
                        "StateProvinceCode": address_data['receiver'].get('state'),
                        "PostalCode": address_data['receiver'].get('zip'),
                        "CountryCode": "US"
                    },
                    "Residential": " "
                },
                "PaymentInformation": {
                    "ShipmentCharge": {
                        "Type": "01",
                        "BillShipper": {"AccountNumber": "C870V5"}
                    }
                },
                "Service": {"Code": "03", "Description": "Express"},
                "Package": {
                    "Description": " ",
                    "Packaging": {"Code": "02", "Description": "Nails"},
                    "Dimensions": {
                        "UnitOfMeasurement": {"Code": "IN", "Description": "Inches"},
                        "Length": "10", "Width": "30", "Height": "45"
                    },
                    "PackageWeight": {
                        "UnitOfMeasurement": {"Code": "LBS", "Description": "Pounds"},
                        "Weight": "5"
                    }
                }
            }
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    try:
        # Send request to UPS Shipment API
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the response code is not 200

        if response.status_code == 200:
            response_data = response.json()
            total_charges = response_data.get('ShipmentResponse', {}).get('ShipmentResults', {}).get('ShipmentCharges', {}).get('TotalCharges', {}).get('MonetaryValue')
            printdata = response_data.get('ShipmentResponse', {}).get('ShipmentResults', {}).get('PackageResults', {})[0].get('ShippingLabel', {}).get('GraphicImage')
            days = calculate_randomized_days()
            if total_charges:
                return {"total_charges": total_charges, "image": printdata, "days" : days}
            else:
                return {"error": "Total charges not found in the response."}
        else:
            return {"error": f"UPS request failed with status code {response.status_code}"}

    except requests.exceptions.RequestException as e:
        return {"error": "Failed to contact UPS API", "details": str(e)}

# Function to handle USPS shipping cost calculation, with error handling for non-numeric input
def usps_shipping_rate(base_cost):
    try:
        base_cost_float = float(base_cost)
    except ValueError:
        return {"error": "Invalid base cost value. It must be a numeric value."}
    
    days = calculate_randomized_days()
    return {"shipping_cost": calculate_randomized_cost(base_cost_float), "days" : days}

@api_view(['POST'])
def get_shipping_rate(request):
    data = request.data
    access_token = data.get("access_token")

    # Validate if access token is provided
    if not access_token:
        return Response({"error": "Access token is missing. Please log in again."}, status=status.HTTP_401_UNAUTHORIZED)

    sender = data.get("sender")
    receiver = data.get("receiver")

    # Validate that sender and receiver data is provided in the request
    if not all([sender, receiver]):
        return Response({"error": "Sender and receiver data are required."}, status=status.HTTP_400_BAD_REQUEST)

    # Connect to the database
    db = get_mongo_db()
    collection = db["shipping_costs"]

    # Check if the record exists in the database
    existing_record = collection.find_one({
        "sender": sender,
        "receiver": receiver
    })

    if existing_record:
        return Response({
            "ups": existing_record.get("ups_cost"),
            "usps": existing_record.get("usps_cost"),
            "uspsdays": existing_record.get("usps_days"),
            "upsdays": existing_record.get("ups_days"),
            "label_url": existing_record.get("label_url"),
            "message": "Data retrieved from database"
        }, status=status.HTTP_200_OK)

    ups_response = ups_shipping(access_token, data)
    if "error" in ups_response:
        return Response(ups_response, status=status.HTTP_400_BAD_REQUEST)

    # Create shipping labels directory if it doesn't exist
    labels_dir = os.path.join(settings.MEDIA_ROOT, 'shipping_labels')
    os.makedirs(labels_dir, exist_ok=True)

    # Generate unique filename for the label
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_id = str(uuid.uuid4())[:8]
    label_filename = f"label_{timestamp}_{unique_id}.gif"
    label_path = os.path.join(labels_dir, label_filename)

    # Save the label image
    base64_string = ups_response.get("image")
    if base64_string:
        try:
            with open(label_path, "wb") as f:
                f.write(base64.b64decode(base64_string))
            print("Label file saved successfully!")
            
            # Generate the URL for the saved label
            label_url = f"{settings.MEDIA_URL}shipping_labels/{label_filename}"
        except Exception as e:
            print(f"Error while saving label: {e}")
            label_url = None
    else:
        label_url = None

    # Calculate USPS shipping rate based on the UPS total charges (if valid)
    usp_cost = float(ups_response.get('total_charges', 0))
    usps_cost = usps_shipping_rate(usp_cost)

    # Check if USPS returned an error message
    if "error" in usps_cost:
        return Response(usps_cost, status=status.HTTP_400_BAD_REQUEST)

    # Save the sender, receiver, and calculated costs to the database
    record = {
        "sender": sender,
        "receiver": receiver,
        "ups_cost": usp_cost,
        "usps_cost": usps_cost.get("shipping_cost"),
        "ups_days": ups_response.get("days"),
        "usps_days": usps_cost.get("days"),
        "label_url": label_url,
        "created_at": datetime.now()
    }
    collection.insert_one(record)

    return Response({
        "ups": usp_cost,
        "usps": usps_cost.get("shipping_cost"),
        "upsdays": ups_response.get("days"),
        "uspsdays": usps_cost.get("days"),
        "label_url": label_url,
        "message": "Data calculated and saved to database"
    }, status=status.HTTP_200_OK)

# Function to process a single CSV row for shipping cost calculation
def process_row(row, access_token):
    sender = {
        "name": row.get("sender_name", "").strip(),
        "phone": row.get("sender_phone", "").strip(),
        "addr": row.get("sender_addr", "").strip(),
        "city": row.get("sender_city", "").strip(),
        "state": row.get("sender_state", "").strip(),
        "zip": row.get("sender_zip", "").strip(),
    }
    receiver = {
        "name": row.get("receiver_name", "").strip(),
        "phone": row.get("receiver_phone", "").strip(),
        "addr": row.get("receiver_addr", "").strip(),
        "city": row.get("receiver_city", "").strip(),
        "state": row.get("receiver_state", "").strip(),
        "zip": row.get("receiver_zip", "").strip(),
    }

    # Connect to the database
    db = get_mongo_db()
    collection = db["shipping_costs"]

    # Check if the record exists in the database
    existing_record = collection.find_one({
        "sender": sender,
        "receiver": receiver
    })

    if existing_record:
        # Return the saved shipping rates from the database
        return {**row, 
                "ups_cost": existing_record.get("ups_cost"),
                "usps_cost": existing_record.get("usps_cost"),
                "upsdays": existing_record.get("ups_days"),
                "uspsdays": existing_record.get("usps_days"),
                "label_url": existing_record.get("label_url"),
                "optimal_service": "UPS" if existing_record.get("ups_cost") <= existing_record.get("usps_cost") else "USPS",
                "optimal_cost": min(existing_record.get("ups_cost"), existing_record.get("usps_cost"))}

    # Fetch UPS shipping rates
    ups_response = ups_shipping(access_token, {"sender": sender, "receiver": receiver})

    if "error" in ups_response:
        return {**row, "error": ups_response["error"]}

    # Create shipping labels directory if it doesn't exist
    labels_dir = os.path.join(settings.MEDIA_ROOT, 'shipping_labels')
    os.makedirs(labels_dir, exist_ok=True)

    # Generate unique filename for the label
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_id = str(uuid.uuid4())[:8]
    label_filename = f"label_{timestamp}_{unique_id}.gif"
    label_path = os.path.join(labels_dir, label_filename)

    # Save the label image
    label_url = None
    base64_string = ups_response.get("image")
    if base64_string:
        try:
            with open(label_path, "wb") as f:
                f.write(base64.b64decode(base64_string))
            print(f"Label file saved successfully for {sender['name']} to {receiver['name']}!")
            
            # Generate the URL for the saved label
            label_url = f"{settings.MEDIA_URL}shipping_labels/{label_filename}"
        except Exception as e:
            print(f"Error while saving label: {e}")

    # Calculate USPS shipping rate
    ups_cost = float(ups_response.get('total_charges', 0))
    usps_cost = usps_shipping_rate(ups_cost)

    if "error" in usps_cost:
        return {**row, "error": usps_cost["error"]}

    # Save the sender, receiver, and calculated costs to the database
    record = {
        "sender": sender,
        "receiver": receiver,
        "ups_cost": ups_cost,
        "ups_days": ups_response.get("days"),
        "usps_days": usps_cost.get("days"),
        "usps_cost": usps_cost.get("shipping_cost"),
        "label_url": label_url,
        "created_at": datetime.now()
    }
    collection.insert_one(record)

    # Determine the optimal service and cost
    optimal_service = "UPS" if ups_cost <= usps_cost.get("shipping_cost") else "USPS"
    optimal_cost = min(ups_cost, usps_cost.get("shipping_cost"))

    return {**row, 
            "ups_cost": ups_cost, 
            "usps_cost": usps_cost.get("shipping_cost"),
            "upsdays": ups_response.get("days"),
            "uspsdays": usps_cost.get("days"),
            "label_url": label_url,
            "optimal_service": optimal_service, 
            "optimal_cost": optimal_cost}

# Bulk CSV shipping rate calculation view
@api_view(['POST'])
@parser_classes([MultiPartParser, FileUploadParser])
def bulk_shipping_rate_calculation(request):
    csv_file = request.FILES.get('file') 
    access_token = request.data.get("access_token")
    
    # Validate the presence of the CSV file and access token
    if not csv_file:
        return Response({"error": "CSV file is missing."}, status=status.HTTP_400_BAD_REQUEST)
    if not access_token:
        return Response({"error": "Access token is missing. Please log in again."}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        # Read the CSV file
        file_data = csv_file.read().decode('utf-8-sig')
        csv_reader = csv.DictReader(StringIO(file_data))

        # Validate required columns
        required_columns = [
            "sender_name", "sender_phone", "sender_addr", "sender_city", "sender_state", "sender_zip",
            "receiver_name", "receiver_phone", "receiver_addr", "receiver_city", "receiver_state", "receiver_zip"
        ]
        
        headers = csv_reader.fieldnames
        if not headers or not all(col in headers for col in required_columns):
            return Response({
                "error": "CSV file is missing required columns. Please ensure all required fields are present.",
                "required_columns": required_columns
            }, status=status.HTTP_400_BAD_REQUEST)

        # Process rows in parallel for faster processing
        with concurrent.futures.ThreadPoolExecutor(max_workers=300) as executor:
            results = list(executor.map(lambda row: process_row(row, access_token), csv_reader))

        # Filter out successful results and errors
        successful_results = [r for r in results if "error" not in r]
        error_results = [r for r in results if "error" in r]

        # Create a response CSV with results
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="bulk_shipping_results.csv"'

        # Get the fieldnames from the first successful result, if any
        if successful_results:
            fieldnames = list(successful_results[0].keys())
        elif error_results:
            fieldnames = list(error_results[0].keys())
        else:
            return Response({"error": "No results generated"}, status=status.HTTP_400_BAD_REQUEST)

        writer = csv.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write all results (both successful and errors)
        writer.writerows(results)

        # Add summary at the end of the CSV
        writer.writerow({})  # Empty row for spacing
        writer.writerow({
            "sender_name": "Summary",
            "ups_cost": f"Total Successful: {len(successful_results)}",
            "usps_cost": f"Total Errors: {len(error_results)}"
        })

        return response

    except Exception as e:
        return Response({
            "error": "An error occurred while processing the CSV file",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def all_details(request):
    db = get_mongo_db()
    collection = db["shipping_costs"]
    
    data = list(collection.find({}))
    
    for item in data:
        item.pop('_id', None)  
    return Response(data)
