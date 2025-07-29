const express = require('express');
const axios = require('axios');
const { dbHelpers } = require('../database/init');

const router = express.Router();

// Jira custom field configuration
// customfield_12319940 is used as the target version field for Red Hat Jira
const TARGET_VERSION_FIELD = 'customfield_12319940';

// Jira API client setup
function createJiraClient(baseURL, token) {
  return axios.create({
    baseURL: `${baseURL}/rest/api/2`,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Accept': 'application/json'
    },
    timeout: 30000
  });
}

// Test Jira connection
router.get('/test', async (req, res) => {
  try {
    const { JIRA_BASE_URL, JIRA_TOKEN } = req.app.locals.config;
    const jira = createJiraClient(JIRA_BASE_URL, JIRA_TOKEN);
    
    const response = await jira.get('/myself');
    res.json({ 
      success: true, 
      user: response.data.displayName,
      message: 'Jira connection successful' 
    });
  } catch (error) {
    console.error('Jira connection failed:', error.message);
    res.status(500).json({ 
      success: false, 
      error: 'Failed to connect to Jira',
      details: error.response?.data || error.message
    });
  }
});

// Get all projects
router.get('/projects', async (req, res) => {
  try {
    const { JIRA_BASE_URL, JIRA_TOKEN } = req.app.locals.config;
    const jira = createJiraClient(JIRA_BASE_URL, JIRA_TOKEN);
    
    const response = await jira.get('/project');
    const projects = response.data.map(project => ({
      key: project.key,
      name: project.name,
      description: project.description,
      projectTypeKey: project.projectTypeKey,
      lead: project.lead?.displayName
    }));
    
    // Store projects in database
    for (const project of projects) {
      await dbHelpers.run(
        `INSERT OR REPLACE INTO projects (jira_key, name, description, updated_at) 
         VALUES (?, ?, ?, CURRENT_TIMESTAMP)`,
        [project.key, project.name, project.description || '']
      );
    }
    
    res.json(projects);
  } catch (error) {
    console.error('Failed to fetch projects:', error.message);
    res.status(500).json({ 
      error: 'Failed to fetch projects',
      details: error.response?.data || error.message
    });
  }
});

// Get versions for a specific project (from custom field)
router.get('/projects/:projectKey/versions', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { JIRA_BASE_URL, JIRA_TOKEN } = req.app.locals.config;
    const jira = createJiraClient(JIRA_BASE_URL, JIRA_TOKEN);
    
    // Get unique values from the custom field across all non-closed issues in the project
    // Note: Some projects may not use this custom field for target versions
    const response = await jira.get('/search', {
      params: {
        jql: `project = ${projectKey} AND type = feature AND status NOT IN (Closed,Done,Resolved)`,
        fields: TARGET_VERSION_FIELD,
        maxResults: 1000
      }
    });
    
    // Extract unique target version values
    const versionSet = new Set();
    response.data.issues.forEach(issue => {
      const customFieldValue = issue.fields[TARGET_VERSION_FIELD];
      if (customFieldValue) {
        if (Array.isArray(customFieldValue)) {
          // Handle array of version objects
          customFieldValue.forEach(version => {
            if (version && version.name) {
              versionSet.add(version.name);
            }
          });
        } else if (typeof customFieldValue === 'string') {
          // Handle string value
          if (customFieldValue.trim() !== '') {
            versionSet.add(customFieldValue);
          }
        } else if (customFieldValue.value || customFieldValue.name) {
          // Handle single object with value or name property
          const versionValue = customFieldValue.value || customFieldValue.name;
          if (versionValue && versionValue.trim() !== '') {
            versionSet.add(versionValue);
          }
        }
      }
    });
    
    const versions = Array.from(versionSet).sort();
    
    console.log(`Found ${versions.length} target versions for project ${projectKey} using ${TARGET_VERSION_FIELD}`);
    
    res.json(versions);
  } catch (error) {
    console.error('Failed to fetch versions:', error.message);
    // Return empty array instead of error for better UX when field doesn't exist
    if (error.response?.data?.errorMessages?.some(msg => 
        msg.includes('does not exist') || msg.includes('cannot be viewed'))) {
      console.log(`Custom field ${TARGET_VERSION_FIELD} not available for ${projectKey}, returning empty versions list`);
      res.json([]);
    } else {
      res.status(500).json({ 
        error: 'Failed to fetch project versions',
        details: error.response?.data || error.message
      });
    }
  }
});

// Get issues for project and target version
router.get('/projects/:projectKey/issues', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion, maxResults = 100 } = req.query;
    const { JIRA_BASE_URL, JIRA_TOKEN } = req.app.locals.config;
    const jira = createJiraClient(JIRA_BASE_URL, JIRA_TOKEN);
    
    let jql = `project = ${projectKey} AND type = feature AND status NOT IN (Closed,Done,Resolved) ORDER BY rank ASC`;
    
    // Note: We fetch all tickets and filter by target version on the application side
    // because some custom fields cannot be used in JQL filters due to permissions
    const response = await jira.get('/search', {
      params: {
        jql,
        maxResults: Math.max(maxResults, 1000), // Fetch more to allow for client-side filtering
        fields: `key,summary,description,issuetype,status,priority,${TARGET_VERSION_FIELD},rank`
      }
    });
    
    let issues = response.data.issues.map(issue => {
      // Extract target version from custom field
      const customFieldValue = issue.fields[TARGET_VERSION_FIELD];
      let targetVersionValue = null;
      
      if (customFieldValue) {
        if (Array.isArray(customFieldValue)) {
          // Handle array of version objects - use the first one
          if (customFieldValue.length > 0 && customFieldValue[0].name) {
            targetVersionValue = customFieldValue[0].name;
          }
        } else if (typeof customFieldValue === 'string') {
          // Handle string value
          targetVersionValue = customFieldValue;
        } else if (customFieldValue.value || customFieldValue.name) {
          // Handle single object with value or name property
          targetVersionValue = customFieldValue.value || customFieldValue.name;
        }
      }
      
      return {
        key: issue.key,
        title: issue.fields.summary,
        description: issue.fields.description,
        issueType: issue.fields.issuetype?.name,
        status: issue.fields.status?.name,
        priority: issue.fields.priority?.name,
        targetVersion: targetVersionValue,
        rank: issue.fields.rank
      };
    });
    
    // Client-side filtering by target version if specified
    if (targetVersion) {
      issues = issues.filter(issue => issue.targetVersion === targetVersion);
    }
    
    // Limit results to the requested maxResults after filtering
    issues = issues.slice(0, maxResults);
    
    // Store issues in database
    const project = await dbHelpers.get('SELECT id FROM projects WHERE jira_key = ?', [projectKey]);
    if (project) {
      for (const issue of issues) {
        await dbHelpers.run(
          `INSERT OR REPLACE INTO tickets 
           (jira_key, project_id, title, description, issue_type, status, priority, target_version, jira_rank, updated_at) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
          [
            issue.key, 
            project.id, 
            issue.title, 
            issue.description || '', 
            issue.issueType, 
            issue.status, 
            issue.priority, 
            issue.targetVersion,
            issue.rank
          ]
        );
      }
    }
    
    res.json({
      total: response.data.total,
      issues
    });
  } catch (error) {
    console.error('Failed to fetch issues:', error.message);
    res.status(500).json({ 
      error: 'Failed to fetch project issues',
      details: error.response?.data || error.message
    });
  }
});

// Update Jira issue rank
router.put('/issues/:issueKey/rank', async (req, res) => {
  try {
    const { issueKey } = req.params;
    const { rank } = req.body;
    const { JIRA_BASE_URL, JIRA_TOKEN } = req.app.locals.config;
    const jira = createJiraClient(JIRA_BASE_URL, JIRA_TOKEN);
    
    // Update rank in Jira
    await jira.put(`/issue/${issueKey}`, {
      fields: {
        rank: rank
      }
    });
    
    // Update rank in local database
    await dbHelpers.run(
      'UPDATE tickets SET jira_rank = ?, updated_at = CURRENT_TIMESTAMP WHERE jira_key = ?',
      [rank, issueKey]
    );
    
    res.json({ success: true, message: 'Rank updated successfully' });
  } catch (error) {
    console.error('Failed to update issue rank:', error.message);
    res.status(500).json({ 
      error: 'Failed to update issue rank',
      details: error.response?.data || error.message
    });
  }
});

// Bulk update ranks based on prioritization results
router.post('/bulk-update-ranks', async (req, res) => {
  try {
    const { projectKey, targetVersion } = req.body;
    const { JIRA_BASE_URL, JIRA_TOKEN } = req.app.locals.config;
    const jira = createJiraClient(JIRA_BASE_URL, JIRA_TOKEN);
    
    // Get aggregated rankings from database
    const rankedTickets = await dbHelpers.all(`
      SELECT 
        t.jira_key,
        t.title,
        AVG(CASE 
          WHEN r.ranking = 'Must have' THEN 4
          WHEN r.ranking = 'Should have' THEN 3
          WHEN r.ranking = 'Could have' THEN 2
          WHEN r.ranking = 'Won''t have' THEN 1
          ELSE 0
        END) as avg_score,
        COUNT(r.id) as vote_count
      FROM tickets t
      LEFT JOIN rankings r ON t.id = r.ticket_id
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ? AND (? IS NULL OR t.target_version = ?) AND t.issue_type = 'Feature'
      GROUP BY t.id, t.jira_key, t.title
      HAVING vote_count > 0
      ORDER BY avg_score DESC, vote_count DESC
    `, [projectKey, targetVersion, targetVersion]);
    
    // Update ranks in Jira (this is a simplified approach - actual Jira rank API may vary)
    const results = [];
    for (let i = 0; i < rankedTickets.length; i++) {
      const ticket = rankedTickets[i];
      const newRank = i + 1;
      
      try {
        // Note: Jira's rank field might require specific API calls depending on the instance
        // This is a basic implementation - might need adjustment based on Jira configuration
        await jira.put(`/issue/${ticket.jira_key}`, {
          fields: {
            rank: newRank
          }
        });
        
        results.push({
          key: ticket.jira_key,
          title: ticket.title,
          newRank,
          avgScore: ticket.avg_score,
          voteCount: ticket.vote_count,
          success: true
        });
      } catch (error) {
        results.push({
          key: ticket.jira_key,
          title: ticket.title,
          newRank,
          avgScore: ticket.avg_score,
          voteCount: ticket.vote_count,
          success: false,
          error: error.message
        });
      }
    }
    
    res.json({
      success: true,
      message: `Updated ranks for ${results.filter(r => r.success).length} tickets`,
      results
    });
  } catch (error) {
    console.error('Failed to bulk update ranks:', error.message);
    res.status(500).json({ 
      error: 'Failed to bulk update ranks',
      details: error.message
    });
  }
});

module.exports = router; 