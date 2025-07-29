const axios = require('axios');

class OllamaService {
  constructor(baseUrl = 'http://localhost:11434', model = 'llama3:latest') {
    this.baseUrl = baseUrl;
    this.model = model;
    this.client = axios.create({
      baseURL: baseUrl,
      timeout: 60000, // 60 seconds timeout for AI responses
    });
  }

  async isAvailable() {
    try {
      const response = await this.client.get('/api/tags');
      return response.status === 200 && response.data.models?.length > 0;
    } catch (error) {
      console.error('Ollama not available:', error.message);
      return false;
    }
  }

  async generateCompletion(prompt) {
    try {
      const response = await this.client.post('/api/generate', {
        model: this.model,
        prompt: prompt,
        stream: false,
        options: {
          temperature: 0.3, // Lower temperature for more consistent analysis
          top_p: 0.9,
          num_predict: 500  // Limit response length
        }
      });
      
      return response.data.response;
    } catch (error) {
      console.error('Error generating completion:', error.message);
      throw new Error(`AI analysis failed: ${error.message}`);
    }
  }

  async analyzeTicket(ticket) {
    const prompt = this.buildAnalysisPrompt(ticket);
    
    try {
      const response = await this.generateCompletion(prompt);
      return this.parseAnalysisResponse(response);
    } catch (error) {
      console.error('Error analyzing ticket:', error.message);
      throw error;
    }
  }

  buildAnalysisPrompt(ticket) {
    return `You are a product management expert analyzing a software development ticket for prioritization.

TICKET DETAILS:
Key: ${ticket.jira_key}
Title: ${ticket.title}
Description: ${ticket.description || 'No description provided'}
Type: ${ticket.issue_type || 'Unknown'}
Status: ${ticket.status || 'Unknown'}
Current Priority: ${ticket.priority || 'Unknown'}

TASK: Provide a concise analysis with exactly these sections:

SUMMARY: Write a 1-2 sentence summary of what this feature/issue does and its main purpose.

BUSINESS_VALUE: Rate the business impact from 1-5 (1=minimal, 5=critical) and explain why in 1 sentence.

TECHNICAL_COMPLEXITY: Rate implementation difficulty from 1-5 (1=simple, 5=very complex) and explain why in 1 sentence.

SUGGESTED_PRIORITY: Based on business value vs complexity, suggest one of: "Must have", "Should have", "Could have", "Won't have" and explain your reasoning in 1 sentence.

USER_IMPACT: Describe who would benefit and how in 1 sentence.

Format your response exactly as shown above with those section headers.`;
  }

  parseAnalysisResponse(response) {
    try {
      const sections = {};
      const lines = response.split('\n');
      let currentSection = null;
      let currentContent = [];

      for (const line of lines) {
        const trimmedLine = line.trim();
        
        // Check if this is a section header (handle both SECTION: and **SECTION**: formats)
        if (trimmedLine.match(/^(\*\*)?(SUMMARY|BUSINESS_VALUE|TECHNICAL_COMPLEXITY|SUGGESTED_PRIORITY|USER_IMPACT)(\*\*)?:/)) {
          // Save previous section if it exists
          if (currentSection && currentContent.length > 0) {
            sections[currentSection] = currentContent.join(' ').trim();
          }
          
          // Start new section
          const parts = trimmedLine.split(':');
          const sectionName = parts[0].replace(/\*\*/g, ''); // Remove asterisks
          currentSection = sectionName.toLowerCase();
          currentContent = parts.slice(1).join(':').trim() ? [parts.slice(1).join(':').trim()] : [];
        } else if (currentSection && trimmedLine) {
          currentContent.push(trimmedLine);
        }
      }

      // Save the last section
      if (currentSection && currentContent.length > 0) {
        sections[currentSection] = currentContent.join(' ').trim();
      }

      // Extract priority suggestion
      let suggestedPriority = null;
      if (sections.suggested_priority) {
        const priorityMatch = sections.suggested_priority.match(/(Must have|Should have|Could have|Won't have)/i);
        if (priorityMatch) {
          suggestedPriority = priorityMatch[1];
        }
      }

      return {
        summary: sections.summary || 'No summary generated',
        businessValue: sections.business_value || 'No business value analysis',
        technicalComplexity: sections.technical_complexity || 'No complexity analysis',
        suggestedPriority: suggestedPriority || 'Could have',
        priorityReasoning: sections.suggested_priority || 'No reasoning provided',
        userImpact: sections.user_impact || 'No user impact analysis',
        rawResponse: response
      };
    } catch (error) {
      console.error('Error parsing AI response:', error.message);
      return {
        summary: 'Failed to parse AI analysis',
        businessValue: 'Analysis unavailable',
        technicalComplexity: 'Analysis unavailable', 
        suggestedPriority: 'Could have',
        priorityReasoning: 'Analysis failed',
        userImpact: 'Analysis unavailable',
        rawResponse: response
      };
    }
  }

  async analyzeBulkTickets(tickets, onProgress = null) {
    const results = [];
    const total = tickets.length;

    for (let i = 0; i < tickets.length; i++) {
      const ticket = tickets[i];
      
      try {
        const analysis = await this.analyzeTicket(ticket);
        results.push({
          jira_key: ticket.jira_key,
          success: true,
          analysis
        });
        
        if (onProgress) {
          onProgress({ completed: i + 1, total, current: ticket.jira_key });
        }
        
        // Add small delay to avoid overwhelming Ollama
        await new Promise(resolve => setTimeout(resolve, 100));
        
      } catch (error) {
        console.error(`Failed to analyze ticket ${ticket.jira_key}:`, error.message);
        results.push({
          jira_key: ticket.jira_key,
          success: false,
          error: error.message
        });
      }
    }

    return results;
  }
}

module.exports = OllamaService; 