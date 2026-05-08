"""
Status Lambda — returns research task status from DynamoDB.

GET /research/{slug}/status
"""
import json
import os
import boto3

TRACKING_TABLE = os.environ['TRACKING_TABLE']
RESEARCH_BUCKET = os.environ['RESEARCH_BUCKET']

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TRACKING_TABLE)


def lambda_handler(event, context):
    """Handle GET /research/{slug}/status."""
    try:
        slug = event.get('pathParameters', {}).get('slug', '')
        if not slug:
            return _response(400, {'error': 'Missing slug parameter'})

        # Extract user ID from JWT
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        user_id = claims.get('sub', 'anonymous')

        # Query task metadata
        result = table.get_item(Key={'pk': f"{user_id}#{slug}", 'sk': 'meta'})
        item = result.get('Item')

        if not item:
            return _response(404, {'error': 'Research task not found'})

        # Build response
        response = {
            'taskId': item.get('taskId'),
            'slug': item.get('slug'),
            'query': item.get('query'),
            'status': item.get('status'),
            'depth': item.get('depth'),
            'createdAt': item.get('createdAt'),
            'updatedAt': item.get('updatedAt'),
            'progress': item.get('progress', []),
            'reportUrl': f'/reports/{slug}/' if item.get('status') == 'COMPLETE' else None,
        }

        # If complete, include cost summary
        if item.get('status') == 'COMPLETE':
            cost_result = table.get_item(Key={'pk': f"{user_id}#{slug}", 'sk': 'cost'})
            cost_item = cost_result.get('Item')
            if cost_item:
                response['cost'] = {
                    'inputTokens': cost_item.get('inputTokens', 0),
                    'outputTokens': cost_item.get('outputTokens', 0),
                    'totalTokens': cost_item.get('totalTokens', 0),
                    'estimatedCostUsd': cost_item.get('estimatedCostUsd', 0),
                }

        return _response(200, response)

    except Exception as e:
        print(f"Error: {e}")
        return _response(500, {'error': 'Internal server error'})


def _response(status_code: int, body: dict) -> dict:
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body),
    }
