const express = require('express');
const { dbHelpers } = require('../database/init');
const { authenticateToken } = require('./auth');

const router = express.Router();

// Get all projects from database
router.get('/', async (req, res) => {
  try {
    const projects = await dbHelpers.all(`
      SELECT 
        p.*,
        COUNT(DISTINCT t.id) as ticket_count,
        COUNT(DISTINCT r.user_id) as participating_users
      FROM projects p
      LEFT JOIN tickets t ON p.id = t.project_id
      LEFT JOIN rankings r ON t.id = r.ticket_id
      GROUP BY p.id
      ORDER BY p.name
    `);
    
    res.json(projects);
  } catch (error) {
    console.error('Failed to fetch projects:', error.message);
    res.status(500).json({ error: 'Failed to fetch projects' });
  }
});

// Get a specific project by key
router.get('/:projectKey', async (req, res) => {
  try {
    const { projectKey } = req.params;
    
    const project = await dbHelpers.get(`
      SELECT 
        p.*,
        COUNT(DISTINCT t.id) as ticket_count,
        COUNT(DISTINCT r.user_id) as participating_users,
        COUNT(DISTINCT r.id) as total_rankings
      FROM projects p
      LEFT JOIN tickets t ON p.id = t.project_id
      LEFT JOIN rankings r ON t.id = r.ticket_id
      WHERE p.jira_key = ?
      GROUP BY p.id
    `, [projectKey]);
    
    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }
    
    res.json(project);
  } catch (error) {
    console.error('Failed to fetch project:', error.message);
    res.status(500).json({ error: 'Failed to fetch project' });
  }
});

// Get all tickets for a project
router.get('/:projectKey/tickets', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion } = req.query;
    
    let sql = `
      SELECT 
        t.*,
        COUNT(r.id) as ranking_count,
        AVG(CASE 
          WHEN r.ranking = 'Must have' THEN 4
          WHEN r.ranking = 'Should have' THEN 3
          WHEN r.ranking = 'Could have' THEN 2
          WHEN r.ranking = 'Won''t have' THEN 1
          ELSE 0
        END) as avg_score
      FROM tickets t
      LEFT JOIN rankings r ON t.id = r.ticket_id
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ? AND t.issue_type = 'Feature'
    `;
    
    const params = [projectKey];
    
    if (targetVersion) {
      sql += ` AND t.target_version = ?`;
      params.push(targetVersion);
    }
    
    sql += ` GROUP BY t.id ORDER BY avg_score DESC, t.title`;
    
    const tickets = await dbHelpers.all(sql, params);
    
    res.json(tickets);
  } catch (error) {
    console.error('Failed to fetch project tickets:', error.message);
    res.status(500).json({ error: 'Failed to fetch project tickets' });
  }
});

// Get unique target versions for a project
router.get('/:projectKey/versions', async (req, res) => {
  try {
    const { projectKey } = req.params;
    
    const versions = await dbHelpers.all(`
      SELECT DISTINCT t.target_version
      FROM tickets t
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ? AND t.target_version IS NOT NULL
      ORDER BY t.target_version
    `, [projectKey]);
    
    res.json(versions.map(v => v.target_version));
  } catch (error) {
    console.error('Failed to fetch project versions:', error.message);
    res.status(500).json({ error: 'Failed to fetch project versions' });
  }
});

// Get project ranking progress
router.get('/:projectKey/progress', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion } = req.query;
    
    let sql = `
      SELECT 
        COUNT(DISTINCT t.id) as total_tickets,
        COUNT(DISTINCT CASE WHEN r.id IS NOT NULL THEN t.id END) as tickets_with_rankings,
        COUNT(DISTINCT r.user_id) as participating_users,
        COUNT(r.id) as total_rankings
      FROM tickets t
      LEFT JOIN rankings r ON t.id = r.ticket_id
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ?
    `;
    
    const params = [projectKey];
    
    if (targetVersion) {
      sql += ` AND t.target_version = ?`;
      params.push(targetVersion);
    }
    
    const progress = await dbHelpers.get(sql, params);
    
    // Calculate completion percentage
    const completionPercentage = progress.total_tickets > 0 
      ? Math.round((progress.tickets_with_rankings / progress.total_tickets) * 100)
      : 0;
    
    res.json({
      ...progress,
      completionPercentage
    });
  } catch (error) {
    console.error('Failed to fetch project progress:', error.message);
    res.status(500).json({ error: 'Failed to fetch project progress' });
  }
});

// Get team participation for a project
router.get('/:projectKey/participation', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion } = req.query;
    
    let sql = `
      SELECT 
        u.id,
        u.username,
        u.email,
        COUNT(r.id) as rankings_submitted,
        MAX(r.updated_at) as last_ranking_date
      FROM users u
      LEFT JOIN rankings r ON u.id = r.user_id
      LEFT JOIN tickets t ON r.ticket_id = t.id
      LEFT JOIN projects p ON t.project_id = p.id AND p.jira_key = ?
    `;
    
    const params = [projectKey];
    
    if (targetVersion) {
      sql += ` AND t.target_version = ?`;
      params.push(targetVersion);
    }
    
    sql += ` GROUP BY u.id ORDER BY rankings_submitted DESC, u.username`;
    
    const participation = await dbHelpers.all(sql, params);
    
    res.json(participation);
  } catch (error) {
    console.error('Failed to fetch team participation:', error.message);
    res.status(500).json({ error: 'Failed to fetch team participation' });
  }
});

// Update project information (admin only)
router.put('/:projectKey', authenticateToken, async (req, res) => {
  try {
    if (req.user.role !== 'admin') {
      return res.status(403).json({ error: 'Admin access required' });
    }
    
    const { projectKey } = req.params;
    const { name, description, target_version } = req.body;
    
    const result = await dbHelpers.run(`
      UPDATE projects 
      SET name = COALESCE(?, name),
          description = COALESCE(?, description),
          target_version = COALESCE(?, target_version),
          updated_at = CURRENT_TIMESTAMP
      WHERE jira_key = ?
    `, [name, description, target_version, projectKey]);
    
    if (result.changes === 0) {
      return res.status(404).json({ error: 'Project not found' });
    }
    
    res.json({ success: true, message: 'Project updated successfully' });
  } catch (error) {
    console.error('Failed to update project:', error.message);
    res.status(500).json({ error: 'Failed to update project' });
  }
});

// Delete a project and all associated data (admin only)
router.delete('/:projectKey', authenticateToken, async (req, res) => {
  try {
    if (req.user.role !== 'admin') {
      return res.status(403).json({ error: 'Admin access required' });
    }
    
    const { projectKey } = req.params;
    
    // Get project ID first
    const project = await dbHelpers.get('SELECT id FROM projects WHERE jira_key = ?', [projectKey]);
    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }
    
    // Delete in order to respect foreign key constraints
    // 1. Delete rankings for tickets in this project
    await dbHelpers.run(`
      DELETE FROM rankings 
      WHERE ticket_id IN (
        SELECT t.id FROM tickets t WHERE t.project_id = ?
      )
    `, [project.id]);
    
    // 2. Delete tickets
    await dbHelpers.run('DELETE FROM tickets WHERE project_id = ?', [project.id]);
    
    // 3. Delete ranking sessions
    await dbHelpers.run('DELETE FROM ranking_sessions WHERE project_id = ?', [project.id]);
    
    // 4. Delete project
    await dbHelpers.run('DELETE FROM projects WHERE id = ?', [project.id]);
    
    res.json({ success: true, message: 'Project and all associated data deleted successfully' });
  } catch (error) {
    console.error('Failed to delete project:', error.message);
    res.status(500).json({ error: 'Failed to delete project' });
  }
});

// Export aggregated rankings as CSV
router.get('/:projectKey/export', async (req, res) => {
  try {
    const { projectKey } = req.params;
    const { targetVersion } = req.query;
    
    let sql = `
      SELECT 
        t.jira_key,
        t.title,
        t.description,
        t.issue_type,
        t.status,
        t.priority,
        t.target_version,
        AVG(CASE 
          WHEN r.ranking = 'Must have' THEN 4
          WHEN r.ranking = 'Should have' THEN 3
          WHEN r.ranking = 'Could have' THEN 2
          WHEN r.ranking = 'Won''t have' THEN 1
          ELSE 0
        END) as avg_score,
        COUNT(r.id) as total_votes,
        SUM(CASE WHEN r.ranking = 'Must have' THEN 1 ELSE 0 END) as must_have_votes,
        SUM(CASE WHEN r.ranking = 'Should have' THEN 1 ELSE 0 END) as should_have_votes,
        SUM(CASE WHEN r.ranking = 'Could have' THEN 1 ELSE 0 END) as could_have_votes,
        SUM(CASE WHEN r.ranking = 'Won''t have' THEN 1 ELSE 0 END) as wont_have_votes
      FROM tickets t
      LEFT JOIN rankings r ON t.id = r.ticket_id
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ?
    `;
    
    const params = [projectKey];
    
    if (targetVersion) {
      sql += ` AND t.target_version = ?`;
      params.push(targetVersion);
    }
    
    sql += ` GROUP BY t.id ORDER BY avg_score DESC, total_votes DESC`;
    
    const data = await dbHelpers.all(sql, params);
    
    // Convert to CSV format
    const headers = [
      'Jira Key', 'Title', 'Description', 'Issue Type', 'Status', 'Priority', 
      'Target Version', 'Average Score', 'Total Votes', 'Must Have', 
      'Should Have', 'Could Have', 'Won\'t Have'
    ];
    
    let csv = headers.join(',') + '\n';
    
    data.forEach(row => {
      const csvRow = [
        `"${row.jira_key || ''}"`,
        `"${(row.title || '').replace(/"/g, '""')}"`,
        `"${(row.description || '').replace(/"/g, '""')}"`,
        `"${row.issue_type || ''}"`,
        `"${row.status || ''}"`,
        `"${row.priority || ''}"`,
        `"${row.target_version || ''}"`,
        row.avg_score || 0,
        row.total_votes || 0,
        row.must_have_votes || 0,
        row.should_have_votes || 0,
        row.could_have_votes || 0,
        row.wont_have_votes || 0
      ];
      csv += csvRow.join(',') + '\n';
    });
    
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', `attachment; filename="${projectKey}-rankings.csv"`);
    res.send(csv);
  } catch (error) {
    console.error('Failed to export rankings:', error.message);
    res.status(500).json({ error: 'Failed to export rankings' });
  }
});

module.exports = router; 