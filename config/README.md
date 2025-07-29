# Configuration Guide

This directory contains the configuration system for the AI Feature Prioritization application.

## Files

- `index.js` - Main configuration module that loads and validates environment variables
- `../env.example` - Template for environment variables (safe to commit)
- `../.env` - Actual environment variables (never commit this file)

## Setup Instructions

1. **Copy the example environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` with your actual values:**
   ```bash
   # Required: Set your JIRA instance URL
   JIRA_BASE_URL=https://your-company.atlassian.net

   # Required: Set your JIRA API token
   JIRA_TOKEN=your_jira_api_token_here

   # Required: Set a secure JWT secret for production
   JWT_SECRET=your_secure_random_string_here
   ```

## Environment Variables

### Server Configuration
- `PORT` - Server port (default: 3001)
- `NODE_ENV` - Environment mode (development/production)

### JIRA Configuration
- `JIRA_BASE_URL` - Your JIRA instance URL (required)
- `JIRA_TOKEN` - JIRA API token (required)

### Authentication
- `JWT_SECRET` - Secret key for JWT tokens (required)

### Database
- `DATABASE_PATH` - SQLite database file path (default: ./database.sqlite)

### AI Configuration
- `OLLAMA_BASE_URL` - Ollama service URL (default: http://localhost:11434)
- `OLLAMA_MODEL` - AI model to use (default: llama2)

## Security Notes

- Never commit `.env` files to version control
- Use strong, random JWT secrets in production
- Rotate JIRA tokens regularly
- Use environment-specific configuration files for different deployments

## Configuration Validation

The application validates all required configuration on startup and will exit with an error if any required values are missing or invalid. 