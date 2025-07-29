const { dbHelpers } = require('../init');

async function addAiAnalysisFields() {
  try {
    console.log('Adding AI analysis fields to tickets table...');
    
    // Add AI analysis columns to tickets table
    const migrations = [
      'ALTER TABLE tickets ADD COLUMN ai_summary TEXT',
      'ALTER TABLE tickets ADD COLUMN ai_business_value TEXT', 
      'ALTER TABLE tickets ADD COLUMN ai_technical_complexity TEXT',
      'ALTER TABLE tickets ADD COLUMN ai_suggested_priority TEXT',
      'ALTER TABLE tickets ADD COLUMN ai_priority_reasoning TEXT',
      'ALTER TABLE tickets ADD COLUMN ai_user_impact TEXT',
      'ALTER TABLE tickets ADD COLUMN ai_analyzed_at DATETIME'
    ];

    for (const migration of migrations) {
      try {
        await dbHelpers.run(migration);
        console.log(`✓ Executed: ${migration}`);
      } catch (error) {
        // Ignore "duplicate column name" errors since this might run multiple times
        if (!error.message.includes('duplicate column name')) {
          console.error(`✗ Failed: ${migration}`, error.message);
          throw error;
        } else {
          console.log(`- Skipped (already exists): ${migration}`);
        }
      }
    }
    
    console.log('AI analysis fields migration completed successfully');
    return true;
  } catch (error) {
    console.error('Migration failed:', error.message);
    throw error;
  }
}

// Run migration if called directly
if (require.main === module) {
  addAiAnalysisFields()
    .then(() => {
      console.log('Migration completed');
      process.exit(0);
    })
    .catch((error) => {
      console.error('Migration failed:', error);
      process.exit(1);
    });
}

module.exports = { addAiAnalysisFields }; 