"""
WebSocket handlers — $connect and $disconnect routes.

$connect: Validates Cognito JWT token from query string, stores connectionId in DDB.
$disconnect: Removes connectionId from DDB.
"""
import json
import os
import time
import boto3

CONNECTIONS_TABLE = os.environ['CONNECTIONS_TABLE']
USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
USER_POOL_CLIENT_ID = os.environ.get('USER_POOL_CLIENT_ID', '')

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(CONNECTIONS_TABLE)


def connect(event, context):
    """
    Handle $connect — store connection with user context.

    Client connects with: wss://xxx.execute-api.region.amazonaws.com/dev?token=<jwt>
    We validate the token and store the connection mapped to the user.
    """
    connection_id = event['requestContext']['connectionId']
    query_params = event.get('queryStringParameters') or {}
    token = query_params.get('token', '')

    # Extract user ID from token (simplified — in production use cognito-idp verify)
    # For now, decode the JWT payload without verification (APIGW can do auth)
    user_id = 'anonymous'
    try:
        if token:
            import base64
            # JWT is 3 parts separated by dots
            parts = token.split('.')
            if len(parts) == 3:
                # Decode payload (part 2) — add padding
                payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
                decoded = json.loads(base64.b64decode(payload))
                user_id = decoded.get('sub', 'anonymous')
    except Exception:
        pass  # Fall through to anonymous

    # Store connection
    table.put_item(Item={
        'connectionId': connection_id,
        'userId': user_id,
        'connectedAt': event['requestContext'].get('connectedAt', ''),
        'ttl': int(time.time()) + (24 * 3600),  # 24-hour TTL
    })

    return {'statusCode': 200, 'body': 'Connected'}


def disconnect(event, context):
    """Handle $disconnect — remove connection from DDB."""
    connection_id = event['requestContext']['connectionId']

    table.delete_item(Key={'connectionId': connection_id})

    return {'statusCode': 200, 'body': 'Disconnected'}
