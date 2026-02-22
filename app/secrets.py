"""
AWS Secrets Manager integration.

This module provides a unified way to load secrets:
- In AWS: Fetches from Secrets Manager
- Locally: Falls back to environment variables

Usage:
    from app.secrets import get_secret

    # Get a single secret value
    db_password = get_secret("DATABASE_PASSWORD")

    # Load all secrets into environment (call once at startup)
    load_secrets_into_env()
"""
from __future__ import annotations

import json
import os
import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# Cache for loaded secrets
_secrets_cache: Optional[dict] = None


def _get_secrets_from_aws(secret_arn: str, region: str) -> dict:
    """
    Fetch secrets from AWS Secrets Manager.

    Args:
        secret_arn: The ARN or name of the secret
        region: AWS region (e.g., 'us-east-1')

    Returns:
        Dictionary of secret key-value pairs
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        logger.warning("boto3 not installed - cannot use AWS Secrets Manager")
        return {}

    try:
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_arn)

        # Secrets Manager stores secrets as JSON string
        if "SecretString" in response:
            return json.loads(response["SecretString"])
        else:
            # Binary secret - not expected for our use case
            logger.warning("Secret is binary, expected JSON string")
            return {}

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "ResourceNotFoundException":
            logger.error(f"Secret not found: {secret_arn}")
        elif error_code == "AccessDeniedException":
            logger.error(f"Access denied to secret: {secret_arn}")
        else:
            logger.error(f"Error fetching secret: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error fetching secrets: {e}")
        return {}


def load_secrets() -> dict:
    """
    Load secrets from AWS Secrets Manager or return empty dict.

    Secrets are cached after first load.

    Environment variables that control this:
        AWS_SECRETS_ARN: ARN or name of the secret in Secrets Manager
        AWS_REGION: AWS region (defaults to us-east-1)

    Returns:
        Dictionary of secrets, or empty dict if not configured/available
    """
    global _secrets_cache

    if _secrets_cache is not None:
        return _secrets_cache

    secret_arn = os.environ.get("AWS_SECRETS_ARN")
    region = os.environ.get("AWS_REGION", "us-east-1")

    if not secret_arn:
        logger.debug("AWS_SECRETS_ARN not set - using environment variables only")
        _secrets_cache = {}
        return _secrets_cache

    logger.info(f"Loading secrets from AWS Secrets Manager: {secret_arn}")
    _secrets_cache = _get_secrets_from_aws(secret_arn, region)
    logger.info(f"Loaded {len(_secrets_cache)} secrets from Secrets Manager")

    return _secrets_cache


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get a secret value by key.

    Checks in order:
    1. Environment variable (allows local override)
    2. AWS Secrets Manager
    3. Default value

    Args:
        key: The secret key name
        default: Default value if not found

    Returns:
        The secret value, or default if not found
    """
    # Environment variables take precedence (useful for local dev/override)
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value

    # Check AWS Secrets Manager
    secrets = load_secrets()
    if key in secrets:
        return secrets[key]

    return default


def load_secrets_into_env() -> int:
    """
    Load all secrets from AWS Secrets Manager into environment variables.

    This is useful for libraries that read directly from os.environ.
    Only sets variables that aren't already set (env vars take precedence).

    Returns:
        Number of secrets loaded into environment
    """
    secrets = load_secrets()
    count = 0

    for key, value in secrets.items():
        if key not in os.environ:
            os.environ[key] = str(value)
            count += 1
            logger.debug(f"Loaded secret into env: {key}")

    return count


def get_database_url() -> Optional[str]:
    """
    Get the database URL, handling AWS RDS secrets format.

    AWS Secrets Manager for RDS stores credentials as:
    {
        "username": "...",
        "password": "...",
        "host": "...",
        "port": 5432,
        "dbname": "..."
    }

    This function constructs a DATABASE_URL from those components
    if a full DATABASE_URL isn't already available.

    Returns:
        PostgreSQL connection string, or None if not configured
    """
    # Check for explicit DATABASE_URL first
    database_url = get_secret("DATABASE_URL")
    if database_url:
        return database_url

    # Try to construct from RDS secret format
    secrets = load_secrets()

    required = ["username", "password", "host", "dbname"]
    if all(k in secrets for k in required):
        username = secrets["username"]
        password = secrets["password"]
        host = secrets["host"]
        port = secrets.get("port", 5432)
        dbname = secrets["dbname"]

        return f"postgresql://{username}:{password}@{host}:{port}/{dbname}"

    return None
