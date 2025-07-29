const express = require('express');
const { dbHelpers } = require('../database/init');

const router = express.Router();

// Submit a ranking for a ticket
router.post('/rank', async (req, res) => {
  try {
    const { userId, ticketKey, ranking, comments } = req.body;
    
    // Validate ranking value
    const validRankings = ['Must have', 'Should have', 'Could have', 'Won\'t have'];
    if (!validRankings.includes(ranking)) {
      return res.status(400).json({ error: 'Invalid ranking value' });
    }
    
    // Get ticket ID from Jira key
    const ticket = await dbHelpers.get('SELECT id FROM tickets WHERE jira_key = ?', [ticketKey]);
    if (!ticket) {
      return res.status(404).json({ error: 'Ticket not found' });
    }
    
    // Insert or update ranking
    await dbHelpers.run(`
      INSERT OR REPLACE INTO rankings (user_id, ticket_id, ranking, comments, updated_at)
      VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    `, [userId, ticket.id, ranking, comments || '']);
    
    res.json({ success: true, message: 'Ranking saved successfully' });
  } catch (error) {
    console.error('Failed to save ranking:', error.message);
    res.status(500).json({ error: 'Failed to save ranking' });
  }
});

// Get rankings for a specific ticket
router.get('/ticket/:ticketKey', async (req, res) => {
  try {
    const { ticketKey } = req.params;
    
    const rankings = await dbHelpers.all(`
      SELECT 
        r.ranking,
        r.comments,
        r.created_at,
        r.updated_at,
        u.username,
        u.email
      FROM rankings r
      JOIN tickets t ON r.ticket_id = t.id
      JOIN users u ON r.user_id = u.id
      WHERE t.jira_key = ?
      ORDER BY r.updated_at DESC
    `, [ticketKey]);
    
    res.json(rankings);
  } catch (error) {
    console.error('Failed to fetch rankings:', error.message);
    res.status(500).json({ error: 'Failed to fetch rankings' });
  }
});

// Get aggregated rankings for a project and target version
router.get('/project/:projectKey/version/:targetVersion/summary', async (req, res) => {
  try {
    const { projectKey, targetVersion } = req.params;
    
    const summary = await dbHelpers.all(`
      SELECT 
        t.jira_key,
        t.title,
        t.description,
        t.issue_type,
        t.status,
        t.priority,
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
      WHERE p.jira_key = ? AND t.target_version = ? AND t.issue_type = 'Feature'
      GROUP BY t.id, t.jira_key, t.title, t.description, t.issue_type, t.status, t.priority
      ORDER BY avg_score DESC, total_votes DESC
    `, [projectKey, targetVersion]);
    
    // Calculate consensus level for each ticket
    const summaryWithConsensus = summary.map(ticket => {
      const totalVotes = ticket.total_votes || 0;
      let consensusLevel = 'No votes';
      
      if (totalVotes > 0) {
        const maxVotes = Math.max(
          ticket.must_have_votes,
          ticket.should_have_votes,
          ticket.could_have_votes,
          ticket.wont_have_votes
        );
        
        const consensusPercentage = (maxVotes / totalVotes) * 100;
        
        if (consensusPercentage >= 75) {
          consensusLevel = 'Strong consensus';
        } else if (consensusPercentage >= 50) {
          consensusLevel = 'Moderate consensus';
        } else {
          consensusLevel = 'No consensus';
        }
      }
      
      return {
        ...ticket,
        consensusLevel,
        consensusPercentage: totalVotes > 0 ? Math.round((Math.max(
          ticket.must_have_votes,
          ticket.should_have_votes,
          ticket.could_have_votes,
          ticket.wont_have_votes
        ) / totalVotes) * 100) : 0
      };
    });
    
    res.json(summaryWithConsensus);
  } catch (error) {
    console.error('Failed to fetch ranking summary:', error.message);
    res.status(500).json({ error: 'Failed to fetch ranking summary' });
  }
});

// Get user's rankings for a project and target version
router.get('/user/:userId/project/:projectKey/version/:targetVersion', async (req, res) => {
  try {
    const { userId, projectKey, targetVersion } = req.params;
    
    const userRankings = await dbHelpers.all(`
      SELECT 
        t.jira_key,
        t.title,
        t.description,
        t.issue_type,
        t.status,
        t.priority,
        r.ranking,
        r.comments,
        r.updated_at
      FROM tickets t
      LEFT JOIN rankings r ON t.id = r.ticket_id AND r.user_id = ?
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ? AND t.target_version = ? AND t.issue_type = 'Feature'
      ORDER BY t.title
    `, [userId, projectKey, targetVersion]);
    
    res.json(userRankings);
  } catch (error) {
    console.error('Failed to fetch user rankings:', error.message);
    res.status(500).json({ error: 'Failed to fetch user rankings' });
  }
});

// Get ranking statistics for a project
router.get('/project/:projectKey/stats', async (req, res) => {
  try {
    const { projectKey } = req.params;
    
    const stats = await dbHelpers.get(`
      SELECT 
        COUNT(DISTINCT t.id) as total_tickets,
        COUNT(DISTINCT r.user_id) as participating_users,
        COUNT(r.id) as total_rankings,
        AVG(CASE 
          WHEN r.ranking = 'Must have' THEN 4
          WHEN r.ranking = 'Should have' THEN 3
          WHEN r.ranking = 'Could have' THEN 2
          WHEN r.ranking = 'Won''t have' THEN 1
          ELSE 0
        END) as avg_overall_score
      FROM tickets t
      LEFT JOIN rankings r ON t.id = r.ticket_id
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ?
    `, [projectKey]);
    
    const rankingDistribution = await dbHelpers.all(`
      SELECT 
        r.ranking,
        COUNT(r.id) as count
      FROM rankings r
      JOIN tickets t ON r.ticket_id = t.id
      JOIN projects p ON t.project_id = p.id
      WHERE p.jira_key = ?
      GROUP BY r.ranking
    `, [projectKey]);
    
    res.json({
      ...stats,
      rankingDistribution
    });
  } catch (error) {
    console.error('Failed to fetch ranking stats:', error.message);
    res.status(500).json({ error: 'Failed to fetch ranking statistics' });
  }
});

// Delete a ranking
router.delete('/user/:userId/ticket/:ticketKey', async (req, res) => {
  try {
    const { userId, ticketKey } = req.params;
    
    // Get ticket ID
    const ticket = await dbHelpers.get('SELECT id FROM tickets WHERE jira_key = ?', [ticketKey]);
    if (!ticket) {
      return res.status(404).json({ error: 'Ticket not found' });
    }
    
    // Delete ranking
    const result = await dbHelpers.run(
      'DELETE FROM rankings WHERE user_id = ? AND ticket_id = ?',
      [userId, ticket.id]
    );
    
    if (result.changes === 0) {
      return res.status(404).json({ error: 'Ranking not found' });
    }
    
    res.json({ success: true, message: 'Ranking deleted successfully' });
  } catch (error) {
    console.error('Failed to delete ranking:', error.message);
    res.status(500).json({ error: 'Failed to delete ranking' });
  }
});

module.exports = router; 