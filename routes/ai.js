const express = require('express');
const router = express.Router();
const { dbHelpers } = require('../database/init');
const OllamaService = require('../services/ollamaService');
const { aiJobQueue } = require('../services/aiJobQueue');

// Initialize Ollama service
const ollama = new OllamaService();

// Check if Ollama is available
router.get('/status', async (req, res) => {
  try {
    const isAvailable = await ollama.isAvailable();
    res.json({ 
      available: isAvailable,
      model: ollama.model,
      baseUrl: ollama.baseUrl 
    });
  } catch (error) {
    res.status(500).json({ 
      error: 'Failed to check Ollama status',
      details: error.message,
      available: false
    });
  }
});

// Analyze a single ticket (background job)
router.post('/analyze/:ticketKey', async (req, res) => {
  try {
    const { ticketKey } = req.params;
    
    // Get ticket details from database
    const ticket = await dbHelpers.get(`
      SELECT t.*, p.name as project_name
      FROM tickets t
      JOIN projects p ON t.project_id = p.id
      WHERE t.jira_key = ? AND t.issue_type = 'Feature'
    `, [ticketKey]);
    
    if (!ticket) {
      return res.status(404).json({ error: 'Ticket not found' });
    }

    // Check if Ollama is available
    const isAvailable = await ollama.isAvailable();
    if (!isAvailable) {
      return res.status(503).json({ error: 'AI analysis service is not available' });
    }

    // Create background job for single analysis
    const jobId = aiJobQueue.createSingleAnalysisJob(ticketKey, ticket);

    res.json({
      message: 'Analysis job started',
      jobId,
      ticketKey,
      status: 'queued'
    });

  } catch (error) {
    console.error('Failed to start ticket analysis:', error.message);
    res.status(500).json({ 
      error: 'Failed to start ticket analysis',
      details: error.message 
    });
  }
});

// Get AI analysis for a ticket
router.get('/analysis/:ticketKey', async (req, res) => {
  try {
    const { ticketKey } = req.params;
    
    const analysis = await dbHelpers.get(`
      SELECT 
        ai_summary,
        ai_business_value,
        ai_technical_complexity,
        ai_suggested_priority,
        ai_priority_reasoning,
        ai_user_impact,
        ai_analyzed_at
      FROM tickets 
      WHERE jira_key = ?
    `, [ticketKey]);
    
    if (!analysis) {
      return res.status(404).json({ error: 'Ticket not found' });
    }

    if (!analysis.ai_analyzed_at) {
      return res.status(404).json({ error: 'No AI analysis found for this ticket' });
    }

    res.json({
      ticketKey,
      analysis: {
        summary: analysis.ai_summary,
        businessValue: analysis.ai_business_value,
        technicalComplexity: analysis.ai_technical_complexity,
        suggestedPriority: analysis.ai_suggested_priority,
        priorityReasoning: analysis.ai_priority_reasoning,
        userImpact: analysis.ai_user_impact,
        analyzedAt: analysis.ai_analyzed_at
      }
    });

  } catch (error) {
    console.error('Failed to get AI analysis:', error.message);
    res.status(500).json({ 
      error: 'Failed to get AI analysis',
      details: error.message 
    });
  }
});

// Start bulk analysis for a project (background job)
router.post('/analyze-project/:projectKey', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion, forceReanalyze = false } = req.body;
    
    // Check if Ollama is available
    const isAvailable = await ollama.isAvailable();
    if (!isAvailable) {
      return res.status(503).json({ error: 'AI analysis service is not available' });
    }

    // Get tickets to analyze
    let whereClause = 'WHERE p.jira_key = ?';
    let params = [projectKey];
    
    if (targetVersion) {
      whereClause += ' AND t.target_version = ?';
      params.push(targetVersion);
    }
    
    if (!forceReanalyze) {
      whereClause += ' AND t.ai_analyzed_at IS NULL';
    }

    const tickets = await dbHelpers.all(`
      SELECT t.*, p.name as project_name
      FROM tickets t
      JOIN projects p ON t.project_id = p.id
      ${whereClause}
      AND t.issue_type = 'Feature'
      ORDER BY t.jira_key
    `, params);
    
    if (tickets.length === 0) {
      return res.json({ 
        message: 'No tickets to analyze',
        ticketsFound: 0,
        jobId: null
      });
    }

    // Create background job
    const jobId = aiJobQueue.createBulkAnalysisJob(projectKey, tickets, {
      targetVersion,
      forceReanalyze
    });

    res.json({
      message: 'Analysis job started',
      jobId,
      ticketsFound: tickets.length,
      status: 'queued'
    });

  } catch (error) {
    console.error('Failed to start project analysis:', error.message);
    res.status(500).json({ 
      error: 'Failed to start project analysis',
      details: error.message 
    });
  }
});

// Get job status
router.get('/job/:jobId', async (req, res) => {
  try {
    const { jobId } = req.params;
    const job = aiJobQueue.getJob(jobId);
    
    if (!job) {
      return res.status(404).json({ error: 'Job not found' });
    }

    res.json({
      id: job.id,
      type: job.type,
      status: job.status,
      progress: job.progress,
      total: job.total,
      results: job.results,
      errors: job.errors,
      createdAt: job.createdAt,
      startedAt: job.startedAt,
      completedAt: job.completedAt
    });

  } catch (error) {
    console.error('Failed to get job status:', error.message);
    res.status(500).json({ 
      error: 'Failed to get job status',
      details: error.message 
    });
  }
});

// Get queue statistics
router.get('/queue/stats', async (req, res) => {
  try {
    const stats = aiJobQueue.getStats();
    res.json(stats);
  } catch (error) {
    console.error('Failed to get queue stats:', error.message);
    res.status(500).json({ 
      error: 'Failed to get queue stats',
      details: error.message 
    });
  }
});

// Get analysis summary for a project
router.get('/project/:projectKey/summary', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion } = req.query;
    
    let whereClause = 'WHERE p.jira_key = ?';
    let params = [projectKey];
    
    if (targetVersion) {
      whereClause += ' AND t.target_version = ?';
      params.push(targetVersion);
    }

    const summary = await dbHelpers.get(`
      SELECT 
        COUNT(*) as total_tickets,
        COUNT(t.ai_analyzed_at) as analyzed_tickets,
        COUNT(CASE WHEN t.ai_suggested_priority = 'Must have' THEN 1 END) as must_have,
        COUNT(CASE WHEN t.ai_suggested_priority = 'Should have' THEN 1 END) as should_have,
        COUNT(CASE WHEN t.ai_suggested_priority = 'Could have' THEN 1 END) as could_have,
        COUNT(CASE WHEN t.ai_suggested_priority = 'Won''t have' THEN 1 END) as wont_have,
        MAX(t.ai_analyzed_at) as last_analysis
      FROM tickets t
      JOIN projects p ON t.project_id = p.id
      ${whereClause}
    `, params);

    res.json({
      projectKey,
      targetVersion: targetVersion || 'all',
      summary
    });

  } catch (error) {
    console.error('Failed to get analysis summary:', error.message);
    res.status(500).json({ 
      error: 'Failed to get analysis summary',
      details: error.message 
    });
  }
});

module.exports = router; 