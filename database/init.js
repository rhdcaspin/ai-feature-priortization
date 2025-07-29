const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const dbPath = process.env.DATABASE_PATH || './database.sqlite';

// Create database connection
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) {
    console.error('Error opening database:', err.message);
  } else {
    console.log('Connected to SQLite database');
    initializeTables();
  }
});

function initializeTables() {
  // Users table for team members
  db.run(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT DEFAULT 'member',
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
  `);

  // Projects table to store Jira projects
  db.run(`
    CREATE TABLE IF NOT EXISTS projects (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      jira_key TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      description TEXT,
      target_version TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
  `);

  // Tickets table to store Jira issues
  db.run(`
    CREATE TABLE IF NOT EXISTS tickets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      jira_key TEXT UNIQUE NOT NULL,
      project_id INTEGER,
      title TEXT NOT NULL,
      description TEXT,
      issue_type TEXT,
      status TEXT,
      priority TEXT,
      target_version TEXT,
      jira_rank INTEGER,
      current_rank REAL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (project_id) REFERENCES projects (id)
    )
  `);

  // Rankings table to store individual user rankings
  db.run(`
    CREATE TABLE IF NOT EXISTS rankings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      ticket_id INTEGER NOT NULL,
      ranking TEXT NOT NULL CHECK (ranking IN ('Must have', 'Should have', 'Could have', 'Won''t have')),
      comments TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (user_id) REFERENCES users (id),
      FOREIGN KEY (ticket_id) REFERENCES tickets (id),
      UNIQUE(user_id, ticket_id)
    )
  `);

  // Ranking sessions table to track prioritization rounds
  db.run(`
    CREATE TABLE IF NOT EXISTS ranking_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id INTEGER NOT NULL,
      target_version TEXT NOT NULL,
      status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'archived')),
      created_by INTEGER NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      completed_at DATETIME,
      FOREIGN KEY (project_id) REFERENCES projects (id),
      FOREIGN KEY (created_by) REFERENCES users (id)
    )
  `);

  console.log('Database tables initialized');
  
  // Run migrations
  runMigrations();
}

async function runMigrations() {
  try {
    // Import and run AI analysis fields migration
    const { addAiAnalysisFields } = require('./migrations/add_ai_analysis_fields');
    await addAiAnalysisFields();
  } catch (error) {
    console.error('Error running migrations:', error.message);
  }
}

// Helper functions for database operations
const dbHelpers = {
  // Run a query with parameters
  run(sql, params = []) {
    return new Promise((resolve, reject) => {
      db.run(sql, params, function(err) {
        if (err) reject(err);
        else resolve({ id: this.lastID, changes: this.changes });
      });
    });
  },

  // Get a single row
  get(sql, params = []) {
    return new Promise((resolve, reject) => {
      db.get(sql, params, (err, row) => {
        if (err) reject(err);
        else resolve(row);
      });
    });
  },

  // Get all rows
  all(sql, params = []) {
    return new Promise((resolve, reject) => {
      db.all(sql, params, (err, rows) => {
        if (err) reject(err);
        else resolve(rows);
      });
    });
  },

  // Close database connection
  close() {
    return new Promise((resolve, reject) => {
      db.close((err) => {
        if (err) reject(err);
        else resolve();
      });
    });
  }
};

module.exports = { db, dbHelpers }; 