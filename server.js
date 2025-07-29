const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const path = require('path');

// Import route modules
const jiraRoutes = require('./routes/jira');
const rankingRoutes = require('./routes/ranking');
const { router: authRoutes } = require('./routes/auth');
const projectRoutes = require('./routes/projects');
const aiRoutes = require('./routes/ai');

// Initialize database
const db = require('./database/init');

const app = express();

// Environment configuration
const PORT = process.env.PORT || 3001;
const JIRA_BASE_URL = process.env.JIRA_BASE_URL || 'https://issues.redhat.com';
const JIRA_TOKEN = process.env.JIRA_TOKEN || 'NzAwMjM1NTU0MTA1Oqqa3A/doAygPOAk7/Hys24aT0GB';
const JWT_SECRET = process.env.JWT_SECRET || 'super_secret_jwt_key_for_development';

// Middleware
app.use(cors());
app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: true }));

// Make config available to routes
app.locals.config = {
  JIRA_BASE_URL,
  JIRA_TOKEN,
  JWT_SECRET
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
    jiraConnected: !!JIRA_TOKEN 
  });
});

// Root endpoint for development
app.get('/', (req, res) => {
  res.json({
    message: 'AI Feature Prioritization API',
    version: '1.0.0',
    endpoints: {
      health: '/api/health',
      jira: '/api/jira',
      projects: '/api/projects',
      ranking: '/api/ranking',
      auth: '/api/auth',
      ai: '/api/ai'
    },
    frontend: process.env.NODE_ENV === 'production' ? 'Served from /build' : 'Run `npm start` in /client folder on port 3000'
  });
});

// Error handling middleware
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(500).json({ error: 'Something went wrong!' });
});

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server running on port ${PORT}`);
  console.log(`Server accessible at http://192.168.1.163:${PORT}`);
  console.log(`Jira URL: ${JIRA_BASE_URL}`);
  console.log('Database initialized');
});

module.exports = app; 