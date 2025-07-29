const JobQueue = require('./jobQueue');
const OllamaService = require('./ollamaService');
const { dbHelpers } = require('../database/init');

class AIJobQueue extends JobQueue {
  constructor() {
    super();
    this.ollama = new OllamaService();
    
    // Set up periodic cleanup
    setInterval(() => {
      this.cleanup();
    }, 60 * 60 * 1000); // Clean up every hour
  }

  // Create a bulk analysis job
  createBulkAnalysisJob(projectKey, tickets, options = {}) {
    return this.createJob('bulk_analysis', {
      projectKey,
      tickets,
      forceReanalyze: options.forceReanalyze || false,
      targetVersion: options.targetVersion
    }, options);
  }

  // Create a single ticket analysis job
  createSingleAnalysisJob(ticketKey, ticket, options = {}) {
    return this.createJob('single_analysis', {
      ticketKey,
      ticket
    }, options);
  }

  // Process different types of jobs
  async processJob(job) {
    switch (job.type) {
      case 'bulk_analysis':
        await this.processBulkAnalysis(job);
        break;
      case 'single_analysis':
        await this.processSingleAnalysis(job);
        break;
      default:
        throw new Error(`Unknown job type: ${job.type}`);
    }
  }

  // Process bulk analysis job
  async processBulkAnalysis(job) {
    const { projectKey, tickets } = job.data;
    
    console.log(`Starting bulk analysis job ${job.id} for project ${projectKey} with ${tickets.length} tickets`);
    
    // Check if Ollama is available
    const isAvailable = await this.ollama.isAvailable();
    if (!isAvailable) {
      throw new Error('Ollama service is not available');
    }

    let processed = 0;
    
    for (const ticket of tickets) {
      try {
        // Analyze the ticket
        const analysis = await this.ollama.analyzeTicket(ticket);
        
        // Store analysis results in database
        await dbHelpers.run(`
          UPDATE tickets SET 
            ai_summary = ?,
            ai_business_value = ?,
            ai_technical_complexity = ?,
            ai_suggested_priority = ?,
            ai_priority_reasoning = ?,
            ai_user_impact = ?,
            ai_analyzed_at = CURRENT_TIMESTAMP
          WHERE jira_key = ?
        `, [
          analysis.summary,
          analysis.businessValue,
          analysis.technicalComplexity,
          analysis.suggestedPriority,
          analysis.priorityReasoning,
          analysis.userImpact,
          ticket.jira_key
        ]);

        processed++;
        
        // Update job progress
        this.updateJobProgress(job.id, processed, {
          ticketKey: ticket.jira_key,
          success: true,
          analysis
        });

        console.log(`Analyzed ticket ${ticket.jira_key} (${processed}/${tickets.length})`);
        
        // Small delay to avoid overwhelming Ollama
        await new Promise(resolve => setTimeout(resolve, 500));

      } catch (error) {
        console.error(`Failed to analyze ticket ${ticket.jira_key}:`, error.message);
        
        processed++;
        
        // Update job progress with error
        this.updateJobProgress(job.id, processed, null, {
          ticketKey: ticket.jira_key,
          error: error.message
        });
      }
    }
    
    console.log(`Completed bulk analysis job ${job.id}: ${processed}/${tickets.length} tickets processed`);
  }

  // Process single analysis job
  async processSingleAnalysis(job) {
    const { ticketKey, ticket } = job.data;
    
    console.log(`Starting single analysis job ${job.id} for ticket ${ticketKey}`);
    
    // Check if Ollama is available
    const isAvailable = await this.ollama.isAvailable();
    if (!isAvailable) {
      throw new Error('Ollama service is not available');
    }

    try {
      // Analyze the ticket
      const analysis = await this.ollama.analyzeTicket(ticket);
      
      // Store analysis results in database
      await dbHelpers.run(`
        UPDATE tickets SET 
          ai_summary = ?,
          ai_business_value = ?,
          ai_technical_complexity = ?,
          ai_suggested_priority = ?,
          ai_priority_reasoning = ?,
          ai_user_impact = ?,
          ai_analyzed_at = CURRENT_TIMESTAMP
        WHERE jira_key = ?
      `, [
        analysis.summary,
        analysis.businessValue,
        analysis.technicalComplexity,
        analysis.suggestedPriority,
        analysis.priorityReasoning,
        analysis.userImpact,
        ticketKey
      ]);

      // Update job progress
      this.updateJobProgress(job.id, 1, {
        ticketKey,
        success: true,
        analysis
      });

      console.log(`Completed single analysis job ${job.id} for ticket ${ticketKey}`);

    } catch (error) {
      console.error(`Failed to analyze ticket ${ticketKey}:`, error.message);
      
      // Update job progress with error
      this.updateJobProgress(job.id, 1, null, {
        ticketKey,
        error: error.message
      });
      
      throw error;
    }
  }

  // Get analysis status for a project
  async getProjectAnalysisStatus(projectKey, targetVersion = null) {
    let whereClause = 'WHERE p.jira_key = ?';
    let params = [projectKey];
    
    if (targetVersion) {
      whereClause += ' AND t.target_version = ?';
      params.push(targetVersion);
    }

    const stats = await dbHelpers.get(`
      SELECT 
        COUNT(*) as total,
        COUNT(t.ai_analyzed_at) as analyzed,
        COUNT(*) - COUNT(t.ai_analyzed_at) as pending
      FROM tickets t
      JOIN projects p ON t.project_id = p.id
      ${whereClause}
    `, params);

    return stats;
  }
}

// Create a singleton instance
const aiJobQueue = new AIJobQueue();

// Export both the class and the singleton
module.exports = {
  AIJobQueue,
  aiJobQueue
}; 