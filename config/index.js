require('dotenv').config();

const config = {
  // Server Configuration
  server: {
    port: process.env.PORT || 3001,
    nodeEnv: process.env.NODE_ENV || 'development',
    host: '0.0.0.0'
  },

  // JIRA Configuration
  jira: {
    baseUrl: process.env.JIRA_BASE_URL,
    token: process.env.JIRA_TOKEN,
    // Validate required JIRA configuration
    validate() {
      if (!this.baseUrl) {
        throw new Error('JIRA_BASE_URL environment variable is required');
      }
      if (!this.token) {
        throw new Error('JIRA_TOKEN environment variable is required');
      }
      return true;
    }
  },

  // Authentication Configuration
  auth: {
    jwtSecret: process.env.JWT_SECRET,
    jwtExpiresIn: '24h',
    validate() {
      if (!this.jwtSecret) {
        throw new Error('JWT_SECRET environment variable is required');
      }
      if (this.jwtSecret === 'super_secret_jwt_key_for_development' && process.env.NODE_ENV === 'production') {
        throw new Error('Please set a secure JWT_SECRET for production');
      }
      return true;
    }
  },

  // Database Configuration
  database: {
    path: process.env.DATABASE_PATH || './database.sqlite'
  },

  // AI Service Configuration
  ai: {
    ollamaBaseUrl: process.env.OLLAMA_BASE_URL || 'http://localhost:11434',
    ollamaModel: process.env.OLLAMA_MODEL || 'llama2',
    enabled: process.env.AI_ENABLED !== 'false'
  },

  // Validate all configurations
  validateAll() {
    try {
      this.jira.validate();
      this.auth.validate();
      console.log('✅ All configuration validated successfully');
      return true;
    } catch (error) {
      console.error('❌ Configuration validation failed:', error.message);
      throw error;
    }
  }
};

module.exports = config; 