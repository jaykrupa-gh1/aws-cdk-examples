# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()

import boto3
from botocore.exceptions import ClientError
import os
import json
import logging
import uuid

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_client = boto3.client("dynamodb")


def handler(event, context):
    table = os.environ.get("TABLE_NAME")
    request_id = context.request_id
    
    # Log request context
    logger.info(json.dumps({
        "event": "request_received",
        "request_id": request_id,
        "source_ip": event.get("requestContext", {}).get("identity", {}).get("sourceIp"),
        "user_agent": event.get("requestContext", {}).get("identity", {}).get("userAgent"),
        "http_method": event.get("requestContext", {}).get("httpMethod"),
    }))
    
    try:
        if event.get("body"):
            item = json.loads(event["body"])
            logger.info(json.dumps({
                "event": "processing_request",
                "request_id": request_id,
                "has_payload": True,
            }))
            year = str(item["year"])
            title = str(item["title"])
            id = str(item["id"])
            
            dynamodb_client.put_item(
                TableName=table,
                Item={"year": {"N": year}, "title": {"S": title}, "id": {"S": id}},
            )
            
            logger.info(json.dumps({
                "event": "dynamodb_write_success",
                "request_id": request_id,
                "table": table,
            }))
            
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"message": "Successfully inserted data!"}),
            }
        else:
            logger.info(json.dumps({
                "event": "processing_request",
                "request_id": request_id,
                "has_payload": False,
            }))
            
            dynamodb_client.put_item(
                TableName=table,
                Item={
                    "year": {"N": "2012"},
                    "title": {"S": "The Amazing Spider-Man 2"},
                    "id": {"S": str(uuid.uuid4())},
                },
            )
            
            logger.info(json.dumps({
                "event": "dynamodb_write_success",
                "request_id": request_id,
                "table": table,
            }))
            
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"message": "Successfully inserted data!"}),
            }
    except ClientError as e:
        if e.response['Error']['Code'] == 'ProvisionedThroughputExceededException':
            logger.warning(json.dumps({
                "event": "dynamodb_throttled",
                "request_id": request_id,
                "table": table,
            }))
            return {
                "statusCode": 429,
                "headers": {
                    "Content-Type": "application/json",
                    "Retry-After": "5"
                },
                "body": json.dumps({"message": "Too many requests, please retry later"}),
            }
        else:
            logger.error(json.dumps({
                "event": "dynamodb_error",
                "request_id": request_id,
                "error_code": e.response['Error']['Code'],
                "error_message": e.response['Error']['Message'],
            }))
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"message": "Internal server error"}),
            }
    except Exception as e:
        logger.error(json.dumps({
            "event": "error",
            "request_id": request_id,
            "error_type": type(e).__name__,
            "error_message": str(e),
        }))
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": "Internal server error"}),
        }
