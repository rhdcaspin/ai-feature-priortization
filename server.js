const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const path = require('path');

// Load configuration
const config = require('./config');

// Import route modules
const jiraRoutes = require('./routes/jira');
const rankingRoutes = require('./routes/ranking');
const { router: authRoutes } = require('./routes/auth');
const projectRoutes = require('./routes/projects');
const aiRoutes = require('./routes/ai');

// Initialize database
const db = require('./database/init');

const app = express();

// Middleware
app.use(cors());
app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: true }));

// Validate configuration on startup
try {
  config.validateAll();
} catch (error) {
  console.error('Server startup failed due to configuration error');
  process.exit(1);
}

// Make config available to routes
app.locals.config = {
  JIRA_BASE_URL: config.jira.baseUrl,
  JIRA_TOKEN: config.jira.token,
  JWT_SECRET: config.auth.jwtSecret
};

// Routes
app.use('/api/jira', jiraRoutes);
app.use('/api/ranking', rankingRoutes);
app.use('/api/auth', authRoutes);
app.use('/api/projects', projectRoutes);
app.use('/api/ai', aiRoutes);

// Serve static files from React app in production
if (process.env.NODE_ENV === 'production') {
  app.use(express.static(path.join(__dirname, 'client/build')));
  
  app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'client/build', 'index.html'));
  });
}

// Health check endpoint
app.get('/api/health', (req, res) => {
  res.json({ 
    status: 'OK', 
    timestamp: new Date().toISOString(),
    jiraConnected: !!config.jira.token,
    aiEnabled: config.ai.enabled
  });
});

// Root endpoint for development
app.get('/', (req, res) => {
  res.json({
    message: 'AI Feature Prioritization API',
    version: '1.0.0',
    environment: config.server.nodeEnv,
    endpoints: {
      health: '/api/health',
      jira: '/api/jira',
      projects: '/api/projects',
      ranking: '/api/ranking',
      auth: '/api/auth',
      ai: '/api/ai'
    },
    frontend: config.server.nodeEnv === 'production' ? 'Served from /build' : 'Run `npm start` in /client folder on port 3000'
  });
});

// Error handling middleware
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(500).json({ error: 'Something went wrong!' });
});

// Start server
app.listen(config.server.port, config.server.host, () => {
  console.log(`🚀 Server running on port ${config.server.port}`);
  console.log(`🌐 Server accessible at http://192.168.1.163:${config.server.port}`);
  console.log(`📋 Jira URL: ${config.jira.baseUrl}`);
  console.log(`🤖 AI Service: ${config.ai.enabled ? 'Enabled' : 'Disabled'}`);
  console.log(`💾 Database initialized`);
});

module.exports = app; 