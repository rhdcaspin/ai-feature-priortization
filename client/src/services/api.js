import axios from 'axios';

// Create axios instance with base configuration
const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor to add auth token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle auth errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// API service methods
export const apiService = {
  // Authentication
  auth: {
    login: (username, password) => api.post('/auth/login', { username, password }),
    register: (username, email, password) => api.post('/auth/register', { username, email, password }),
    me: () => api.get('/auth/me'),
    setupAdmin: (username, email, password) => api.post('/auth/setup-admin', { username, email, password }),
    users: () => api.get('/auth/users'),
  },

  // Jira integration
  jira: {
    test: () => api.get('/jira/test'),
    getProjects: () => api.get('/jira/projects'),
    getProjectVersions: (projectKey) => api.get(`/jira/projects/${projectKey}/versions`),
    getProjectIssues: (projectKey, params = {}) => api.get(`/jira/projects/${projectKey}/issues`, { params }),
    updateIssueRank: (issueKey, rank) => api.put(`/jira/issues/${issueKey}/rank`, { rank }),
    bulkUpdateRanks: (projectKey, targetVersion) => api.post('/jira/bulk-update-ranks', { projectKey, targetVersion }),
  },

  // Projects
  projects: {
    getAll: () => api.get('/projects'),
    getByKey: (projectKey) => api.get(`/projects/${projectKey}`),
    getTickets: (projectKey, params = {}) => api.get(`/projects/${projectKey}/tickets`, { params }),
    getVersions: (projectKey) => api.get(`/projects/${projectKey}/versions`),
    getProgress: (projectKey, params = {}) => api.get(`/projects/${projectKey}/progress`, { params }),
    getParticipation: (projectKey, params = {}) => api.get(`/projects/${projectKey}/participation`, { params }),
    update: (projectKey, data) => api.put(`/projects/${projectKey}`, data),
    delete: (projectKey) => api.delete(`/projects/${projectKey}`),
    export: (projectKey, params = {}) => api.get(`/projects/${projectKey}/export`, { params }),
  },

  // Rankings
  rankings: {
    submit: (userId, ticketKey, ranking, comments) => 
      api.post('/ranking/rank', { userId, ticketKey, ranking, comments }),
    getTicketRankings: (ticketKey) => api.get(`/ranking/ticket/${ticketKey}`),
    getProjectSummary: (projectKey, targetVersion) => 
      api.get(`/ranking/project/${projectKey}/version/${targetVersion}/summary`),
    getUserRankings: (userId, projectKey, targetVersion) => 
      api.get(`/ranking/user/${userId}/project/${projectKey}/version/${targetVersion}`),
    getProjectStats: (projectKey) => api.get(`/ranking/project/${projectKey}/stats`),
    deleteRanking: (userId, ticketKey) => api.delete(`/ranking/user/${userId}/ticket/${ticketKey}`),
  },

  // AI Analysis
  ai: {
    status: () => api.get('/ai/status'),
    analyzeTicket: (ticketKey) => api.post(`/ai/analyze/${ticketKey}`),
    analyzeProject: (projectKey, { targetVersion, forceReanalyze = false } = {}) => 
      api.post(`/ai/analyze-project/${projectKey}`, { targetVersion, forceReanalyze }),
    getJobStatus: (jobId) => api.get(`/ai/job/${jobId}`),
    getQueueStats: () => api.get('/ai/queue/stats'),
    getAnalysis: (ticketKey) => api.get(`/ai/analysis/${ticketKey}`),
    getProjectSummary: (projectKey, targetVersion = '') => 
      api.get(`/ai/project/${projectKey}/summary${targetVersion ? `?targetVersion=${targetVersion}` : ''}`),
  },

  // Health check
  health: () => api.get('/health'),
};

export default api; 