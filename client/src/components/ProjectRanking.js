import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import {
  Typography,
  Box,
  Card,
  CardContent,
  Grid,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Alert,
  CircularProgress,
  Chip,
  Collapse,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  LinearProgress,
  Link
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Save as SaveIcon,
  Visibility as VisibilityIcon,
  Download as ExportIcon,
  Psychology as AiIcon,
  AutoAwesome as MagicIcon,
  TrendingUp as TrendingUpIcon
} from '@mui/icons-material';
import { useAuth } from '../App';
import { apiService } from '../services/api';

const RANKING_OPTIONS = [
  { value: 'Must have', label: 'Must have', color: '#ff5722' },
  { value: 'Should have', label: 'Should have', color: '#ff9800' },
  { value: 'Could have', label: 'Could have', color: '#4caf50' },
  { value: "Won't have", label: "Won't have (this time)", color: '#9e9e9e' }
];

const ProjectRanking = () => {
  const { projectKey } = useParams();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  
  const [tickets, setTickets] = useState([]);
  const [userRankings, setUserRankings] = useState({});
  const [aggregatedRankings, setAggregatedRankings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState({});
  const [error, setError] = useState('');
  const [expandedTickets, setExpandedTickets] = useState({});
  const [viewMode, setViewMode] = useState('ranking'); // 'ranking' or 'results'
  const [showRankingDetails, setShowRankingDetails] = useState(false);
  const [selectedTicketDetails, setSelectedTicketDetails] = useState(null);
  const [aiAnalyses, setAiAnalyses] = useState({});
  const [aiStatus, setAiStatus] = useState({ available: false, checking: true });
  const [analyzingTickets, setAnalyzingTickets] = useState({});
  const [bulkAnalyzing, setBulkAnalyzing] = useState(false);
  const [bulkAnalysisProgress, setBulkAnalysisProgress] = useState({ completed: 0, total: 0 });
  
  const targetVersion = searchParams.get('version');

  // Define loadAggregatedRankings first since loadProjectData depends on it
  const loadAggregatedRankings = useCallback(async () => {
    try {
      const response = await apiService.rankings.getProjectSummary(projectKey, targetVersion || '');
      setAggregatedRankings(response.data);
    } catch (error) {
      console.error('Failed to load aggregated rankings:', error);
    }
  }, [projectKey, targetVersion]);

  const loadProjectData = useCallback(async () => {
    try {
      setLoading(true);
      setError('');

      // Load tickets and user rankings in parallel
      const [ticketsResponse, userRankingsResponse] = await Promise.all([
        apiService.projects.getTickets(projectKey, { targetVersion }),
        apiService.rankings.getUserRankings(user.id, projectKey, targetVersion || '')
      ]);

      setTickets(ticketsResponse.data);
      
      // Convert user rankings to a map for easy lookup
      const rankingsMap = {};
      userRankingsResponse.data.forEach(ranking => {
        rankingsMap[ranking.jira_key] = {
          ranking: ranking.ranking,
          comments: ranking.comments
        };
      });
      setUserRankings(rankingsMap);

      // Load aggregated rankings if in results view
      if (viewMode === 'results') {
        loadAggregatedRankings();
      }
    } catch (error) {
      setError('Failed to load project data: ' + (error.response?.data?.error || error.message));
    } finally {
      setLoading(false);
    }
  }, [projectKey, targetVersion, user.id, viewMode, loadAggregatedRankings]);

  // Check AI status and load existing analyses
  const checkAiStatus = useCallback(async () => {
    try {
      const response = await apiService.ai.status();
      setAiStatus({ available: response.data.available, checking: false });
    } catch (error) {
      console.error('Failed to check AI status:', error);
      setAiStatus({ available: false, checking: false });
    }
  }, []);

  const loadAiAnalyses = useCallback(async () => {
    if (!tickets.length) return;
    
    const analyses = {};
    for (const ticket of tickets) {
      try {
        const response = await apiService.ai.getAnalysis(ticket.jira_key);
        if (response.data.analysis) {
          analyses[ticket.jira_key] = response.data.analysis;
        }
      } catch (error) {
        // Ignore 404 errors for tickets without analysis
        if (error.response?.status !== 404) {
          console.error(`Failed to load analysis for ${ticket.jira_key}:`, error);
        }
      }
    }
    setAiAnalyses(analyses);
  }, [tickets]);

  const analyzeTicket = async (ticketKey) => {
    try {
      setAnalyzingTickets(prev => ({ ...prev, [ticketKey]: true }));
      const response = await apiService.ai.analyzeTicket(ticketKey);
      
      if (response.data.jobId) {
        // Poll for job completion
        pollSingleTicketJob(response.data.jobId, ticketKey);
      }
      
    } catch (error) {
      setError('Failed to start ticket analysis: ' + (error.response?.data?.error || error.message));
      setAnalyzingTickets(prev => ({ ...prev, [ticketKey]: false }));
    }
  };

  const pollSingleTicketJob = async (jobId, ticketKey) => {
    let pollInterval;
    
    const checkStatus = async () => {
      try {
        const response = await apiService.ai.getJobStatus(jobId);
        const job = response.data;
        
        if (job.status === 'completed' || job.status === 'failed') {
          setAnalyzingTickets(prev => ({ ...prev, [ticketKey]: false }));
          clearInterval(pollInterval);
          
          if (job.status === 'completed' && job.results.length > 0) {
            const result = job.results[0];
            if (result.success && result.analysis) {
              setAiAnalyses(prev => ({
                ...prev,
                [ticketKey]: result.analysis
              }));
            }
          } else if (job.status === 'failed') {
            setError('Failed to analyze ticket: ' + (job.errors[0]?.error || 'Unknown error'));
          }
        }
        
      } catch (error) {
        console.error('Error polling single ticket job:', error);
        setAnalyzingTickets(prev => ({ ...prev, [ticketKey]: false }));
        clearInterval(pollInterval);
        setError('Failed to get analysis status');
      }
    };
    
    // Poll every 1 second for individual tickets
    pollInterval = setInterval(checkStatus, 1000);
    
    // Check immediately
    checkStatus();
  };

  const analyzeBulkTickets = async () => {
    if (!aiStatus.available) {
      setError('AI analysis service is not available');
      return;
    }

    try {
      setBulkAnalyzing(true);
      setBulkAnalysisProgress({ completed: 0, total: 0 });
      setError('');

      // Start the bulk analysis job
      const response = await apiService.ai.analyzeProject(projectKey, {
        targetVersion,
        forceReanalyze: false
      });

      if (response.data.jobId) {
        // Poll for job status
        pollJobStatus(response.data.jobId);
      } else {
        // No tickets to analyze
        setBulkAnalyzing(false);
        setBulkAnalysisProgress({ completed: 0, total: 0 });
      }

    } catch (error) {
      setError('Failed to start bulk analysis: ' + (error.response?.data?.error || error.message));
      setBulkAnalyzing(false);
    }
  };

  const pollJobStatus = async (jobId) => {
    let pollInterval;
    
    const checkStatus = async () => {
      try {
        const response = await apiService.ai.getJobStatus(jobId);
        const job = response.data;
        
        setBulkAnalysisProgress({ completed: job.progress, total: job.total });
        
        // Update AI analyses with completed results
        job.results.forEach(result => {
          if (result.success && result.analysis) {
            setAiAnalyses(prev => ({
              ...prev,
              [result.ticketKey]: result.analysis
            }));
          }
        });
        
        if (job.status === 'completed' || job.status === 'failed') {
          setBulkAnalyzing(false);
          clearInterval(pollInterval);
          
          if (job.status === 'failed') {
            setError('Bulk analysis failed. Check server logs for details.');
          } else if (job.errors.length > 0) {
            console.warn('Some tickets failed to analyze:', job.errors);
          }
        }
        
      } catch (error) {
        console.error('Error polling job status:', error);
        setBulkAnalyzing(false);
        clearInterval(pollInterval);
        setError('Failed to get job status');
      }
    };
    
    // Poll every 2 seconds
    pollInterval = setInterval(checkStatus, 2000);
    
    // Check immediately
    checkStatus();
  };

  useEffect(() => {
    loadProjectData();
    checkAiStatus();
  }, [loadProjectData, checkAiStatus]);

  useEffect(() => {
    if (tickets.length > 0) {
      loadAiAnalyses();
    }
  }, [tickets.length, loadAiAnalyses]);

  const handleRankingChange = (ticketKey, ranking) => {
    setUserRankings(prev => ({
      ...prev,
      [ticketKey]: {
        ...prev[ticketKey],
        ranking
      }
    }));
  };

  const handleCommentsChange = (ticketKey, comments) => {
    setUserRankings(prev => ({
      ...prev,
      [ticketKey]: {
        ...prev[ticketKey],
        comments
      }
    }));
  };

  const handleSaveRanking = async (ticketKey) => {
    const ranking = userRankings[ticketKey];
    if (!ranking?.ranking) return;

    try {
      setSaving(prev => ({ ...prev, [ticketKey]: true }));
      
      await apiService.rankings.submit(
        user.id,
        ticketKey,
        ranking.ranking,
        ranking.comments || ''
      );

      // Show success feedback
      setError('');
    } catch (error) {
      setError('Failed to save ranking: ' + (error.response?.data?.error || error.message));
    } finally {
      setSaving(prev => ({ ...prev, [ticketKey]: false }));
    }
  };

  const handleToggleExpand = (ticketKey) => {
    setExpandedTickets(prev => ({
      ...prev,
      [ticketKey]: !prev[ticketKey]
    }));
  };

  const handleViewModeChange = (mode) => {
    setViewMode(mode);
    if (mode === 'results') {
      loadAggregatedRankings();
    }
  };

  const handleViewTicketDetails = async (ticketKey) => {
    try {
      const response = await apiService.rankings.getTicketRankings(ticketKey);
      setSelectedTicketDetails({
        ticketKey,
        rankings: response.data
      });
      setShowRankingDetails(true);
    } catch (error) {
      setError('Failed to load ticket details: ' + (error.response?.data?.error || error.message));
    }
  };

  const getRankingColor = (ranking) => {
    const option = RANKING_OPTIONS.find(opt => opt.value === ranking);
    return option ? option.color : '#9e9e9e';
  };

  const getConsensusClass = (consensusLevel) => {
    switch (consensusLevel) {
      case 'Strong consensus': return 'consensus-strong';
      case 'Moderate consensus': return 'consensus-moderate';
      default: return 'consensus-none';
    }
  };

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="50vh">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Typography variant="h4" component="h1">
          {projectKey} - Feature Ranking
          {targetVersion && (
            <Chip 
              label={`Version: ${targetVersion}`} 
              color="primary" 
              sx={{ ml: 2 }} 
            />
          )}
        </Typography>
        
        <Box display="flex" gap={1}>
          <Button
            variant={viewMode === 'ranking' ? 'contained' : 'outlined'}
            onClick={() => handleViewModeChange('ranking')}
          >
            My Rankings
          </Button>
          <Button
            variant={viewMode === 'results' ? 'contained' : 'outlined'}
            onClick={() => handleViewModeChange('results')}
            startIcon={<VisibilityIcon />}
          >
            Team Results
          </Button>
        </Box>
      </Box>

      {/* AI Analysis Controls */}
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3} p={2} 
           sx={{ backgroundColor: '#f5f5f5', borderRadius: 1 }}>
        <Box display="flex" alignItems="center" gap={2}>
          <AiIcon color="primary" />
          <Typography variant="h6">
            AI Analysis
          </Typography>
          <Chip 
            label={aiStatus.checking ? 'Checking...' : aiStatus.available ? 'Available' : 'Unavailable'} 
            color={aiStatus.available ? 'success' : 'error'}
            size="small"
          />
        </Box>
        
        <Box display="flex" gap={1}>
          <Button
            variant="outlined"
            startIcon={bulkAnalyzing ? <CircularProgress size={16} /> : <MagicIcon />}
            onClick={analyzeBulkTickets}
            disabled={!aiStatus.available || bulkAnalyzing}
          >
            {bulkAnalyzing ? `Analyzing (${bulkAnalysisProgress.completed}/${bulkAnalysisProgress.total})` : 'Analyze All'}
          </Button>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {viewMode === 'ranking' && (
        <Box>
          <Typography variant="h6" gutterBottom>
            Rank each feature using the MoSCoW method:
          </Typography>
          
          <Box display="flex" gap={1} mb={3} flexWrap="wrap">
            {RANKING_OPTIONS.map(option => (
              <Chip
                key={option.value}
                label={option.label}
                sx={{ 
                  backgroundColor: option.color, 
                  color: 'white',
                  fontWeight: 'bold'
                }}
              />
            ))}
          </Box>

          {tickets.map(ticket => (
            <Card key={ticket.jira_key} className="ranking-card" sx={{ mb: 2 }}>
              <CardContent>
                <Grid container spacing={2} alignItems="center">
                  <Grid item xs={12} md={6}>
                    <Box>
                      <Link 
                        href={`https://issues.redhat.com/browse/${ticket.jira_key}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        underline="hover"
                        sx={{ textDecoration: 'none' }}
                      >
                        <Typography variant="h6" color="primary">
                          {ticket.jira_key}
                        </Typography>
                      </Link>
                      <Typography variant="subtitle1" gutterBottom>
                        {ticket.title}
                      </Typography>
                      <Box display="flex" gap={1} flexWrap="wrap">
                        {ticket.issue_type && (
                          <Chip size="small" label={ticket.issue_type} />
                        )}
                        {ticket.priority && (
                          <Chip size="small" label={ticket.priority} color="secondary" />
                        )}
                        {ticket.status && (
                          <Chip size="small" label={ticket.status} variant="outlined" />
                        )}
                      </Box>
                    </Box>
                  </Grid>

                  <Grid item xs={12} md={4}>
                    <FormControl fullWidth size="small">
                      <InputLabel>Ranking</InputLabel>
                      <Select
                        value={userRankings[ticket.jira_key]?.ranking || ''}
                        onChange={(e) => handleRankingChange(ticket.jira_key, e.target.value)}
                        label="Ranking"
                      >
                        {RANKING_OPTIONS.map(option => (
                          <MenuItem key={option.value} value={option.value}>
                            <Box display="flex" alignItems="center" gap={1}>
                              <Box
                                sx={{
                                  width: 16,
                                  height: 16,
                                  backgroundColor: option.color,
                                  borderRadius: '50%'
                                }}
                              />
                              {option.label}
                            </Box>
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </Grid>

                  <Grid item xs={12} md={2}>
                    <Box display="flex" gap={1}>
                      <IconButton
                        onClick={() => handleToggleExpand(ticket.jira_key)}
                        size="small"
                      >
                        {expandedTickets[ticket.jira_key] ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                      </IconButton>
                      <Button
                        variant="contained"
                        size="small"
                        onClick={() => handleSaveRanking(ticket.jira_key)}
                        disabled={!userRankings[ticket.jira_key]?.ranking || saving[ticket.jira_key]}
                        startIcon={saving[ticket.jira_key] ? <CircularProgress size={16} /> : <SaveIcon />}
                      >
                        Save
                      </Button>
                    </Box>
                  </Grid>
                </Grid>

                <Collapse in={expandedTickets[ticket.jira_key]}>
                  <Box sx={{ mt: 2 }}>
                    {ticket.description && (
                      <Typography variant="body2" paragraph>
                        <strong>Description:</strong> {ticket.description}
                      </Typography>
                    )}
                    
                    {/* AI Analysis Section */}
                    <Box sx={{ mb: 2, p: 2, backgroundColor: '#f8f9fa', borderRadius: 1 }}>
                      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: 1 }}>
                          <AiIcon fontSize="small" color="primary" />
                          AI Analysis
                        </Typography>
                        {!aiAnalyses[ticket.jira_key] && (
                          <Button
                            size="small"
                            variant="outlined"
                            startIcon={analyzingTickets[ticket.jira_key] ? <CircularProgress size={16} /> : <MagicIcon />}
                            onClick={() => analyzeTicket(ticket.jira_key)}
                            disabled={!aiStatus.available || analyzingTickets[ticket.jira_key]}
                          >
                            {analyzingTickets[ticket.jira_key] ? 'Analyzing...' : 'Analyze'}
                          </Button>
                        )}
                      </Box>
                      
                      {aiAnalyses[ticket.jira_key] ? (
                        <Box>
                          <Grid container spacing={2}>
                            <Grid item xs={12}>
                              <Typography variant="body2" paragraph>
                                <strong>Summary:</strong> {aiAnalyses[ticket.jira_key].summary}
                              </Typography>
                            </Grid>
                            
                            <Grid item xs={12} md={6}>
                              <Typography variant="body2" paragraph>
                                <strong>Business Value:</strong> {aiAnalyses[ticket.jira_key].businessValue}
                              </Typography>
                            </Grid>
                            
                            <Grid item xs={12} md={6}>
                              <Typography variant="body2" paragraph>
                                <strong>Technical Complexity:</strong> {aiAnalyses[ticket.jira_key].technicalComplexity}
                              </Typography>
                            </Grid>
                            
                            <Grid item xs={12}>
                              <Box display="flex" alignItems="center" gap={1} mb={1}>
                                <TrendingUpIcon fontSize="small" color="primary" />
                                <Typography variant="body2" sx={{ fontWeight: 'bold' }}>
                                  AI Suggested Priority:
                                </Typography>
                                <Chip
                                  label={aiAnalyses[ticket.jira_key].suggestedPriority}
                                  size="small"
                                  sx={{
                                    backgroundColor: RANKING_OPTIONS.find(opt => opt.value === aiAnalyses[ticket.jira_key].suggestedPriority)?.color || '#9e9e9e',
                                    color: 'white',
                                    fontWeight: 'bold'
                                  }}
                                />
                              </Box>
                              <Typography variant="body2" paragraph>
                                <strong>Reasoning:</strong> {aiAnalyses[ticket.jira_key].priorityReasoning}
                              </Typography>
                            </Grid>
                            
                            <Grid item xs={12}>
                              <Typography variant="body2">
                                <strong>User Impact:</strong> {aiAnalyses[ticket.jira_key].userImpact}
                              </Typography>
                            </Grid>
                          </Grid>
                        </Box>
                      ) : (
                        <Typography variant="body2" color="text.secondary">
                          {!aiStatus.available ? 'AI analysis service is not available' : 'No AI analysis available. Click "Analyze" to generate insights.'}
                        </Typography>
                      )}
                    </Box>
                    
                    <TextField
                      fullWidth
                      label="Comments (optional)"
                      multiline
                      rows={2}
                      value={userRankings[ticket.jira_key]?.comments || ''}
                      onChange={(e) => handleCommentsChange(ticket.jira_key, e.target.value)}
                      size="small"
                    />
                  </Box>
                </Collapse>
              </CardContent>
            </Card>
          ))}
        </Box>
      )}

      {viewMode === 'results' && (
        <Box>
          <Box display="flex" justify="space-between" alignItems="center" mb={3}>
            <Typography variant="h6">
              Aggregated Team Rankings
            </Typography>
            <Button
              variant="outlined"
              startIcon={<ExportIcon />}
              onClick={() => window.open(`/api/projects/${projectKey}/export${targetVersion ? `?targetVersion=${targetVersion}` : ''}`, '_blank')}
            >
              Export CSV
            </Button>
          </Box>

          {aggregatedRankings.map((ticket, index) => (
            <Card 
              key={ticket.jira_key} 
              className={`ranking-card ${getConsensusClass(ticket.consensusLevel)}`}
              sx={{ mb: 2 }}
            >
              <CardContent>
                <Grid container spacing={2} alignItems="center">
                  <Grid item xs={1}>
                    <Typography variant="h5" color="primary">
                      #{index + 1}
                    </Typography>
                  </Grid>
                  
                  <Grid item xs={6}>
                    <Link 
                      href={`https://issues.redhat.com/browse/${ticket.jira_key}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      underline="hover"
                      sx={{ textDecoration: 'none' }}
                    >
                      <Typography variant="h6" color="primary">
                        {ticket.jira_key}
                      </Typography>
                    </Link>
                    <Typography variant="subtitle1">
                      {ticket.title}
                    </Typography>
                    <Box display="flex" gap={1} flexWrap="wrap" mt={1}>
                      <Chip size="small" label={ticket.issue_type} />
                      <Chip size="small" label={ticket.status} variant="outlined" />
                      <Chip 
                        size="small" 
                        label={`${ticket.consensusLevel} (${ticket.consensusPercentage}%)`}
                        color={ticket.consensusLevel === 'Strong consensus' ? 'success' : 
                               ticket.consensusLevel === 'Moderate consensus' ? 'warning' : 'error'}
                      />
                    </Box>
                  </Grid>

                  <Grid item xs={3}>
                    <Typography variant="body2" gutterBottom>
                      <strong>Score:</strong> {ticket.avg_score?.toFixed(2) || '0.00'} / 4.00
                    </Typography>
                    <LinearProgress 
                      variant="determinate" 
                      value={(ticket.avg_score || 0) * 25} 
                      sx={{ mb: 1 }}
                    />
                    <Typography variant="body2">
                      <strong>Votes:</strong> {ticket.total_votes || 0}
                    </Typography>
                  </Grid>

                  <Grid item xs={2}>
                    <Box display="flex" gap={1}>
                      <Box textAlign="center">
                        <Typography variant="caption" display="block">Must</Typography>
                        <Typography variant="h6" color="error">
                          {ticket.must_have_votes || 0}
                        </Typography>
                      </Box>
                      <Box textAlign="center">
                        <Typography variant="caption" display="block">Should</Typography>
                        <Typography variant="h6" color="warning.main">
                          {ticket.should_have_votes || 0}
                        </Typography>
                      </Box>
                      <Box textAlign="center">
                        <Typography variant="caption" display="block">Could</Typography>
                        <Typography variant="h6" color="success.main">
                          {ticket.could_have_votes || 0}
                        </Typography>
                      </Box>
                      <Box textAlign="center">
                        <Typography variant="caption" display="block">Won't</Typography>
                        <Typography variant="h6" color="text.secondary">
                          {ticket.wont_have_votes || 0}
                        </Typography>
                      </Box>
                    </Box>
                    <Button
                      size="small"
                      onClick={() => handleViewTicketDetails(ticket.jira_key)}
                      sx={{ mt: 1 }}
                    >
                      Details
                    </Button>
                  </Grid>
                </Grid>
              </CardContent>
            </Card>
          ))}
        </Box>
      )}

      {/* Ticket Details Dialog */}
      <Dialog 
        open={showRankingDetails} 
        onClose={() => setShowRankingDetails(false)}
        maxWidth="md" 
        fullWidth
      >
        <DialogTitle>
          Ranking Details: {' '}
          <Link 
            href={`https://issues.redhat.com/browse/${selectedTicketDetails?.ticketKey}`}
            target="_blank"
            rel="noopener noreferrer"
            underline="hover"
          >
            {selectedTicketDetails?.ticketKey}
          </Link>
        </DialogTitle>
        <DialogContent>
          {selectedTicketDetails?.rankings.map((ranking, index) => (
            <Box key={index} sx={{ mb: 2, p: 2, border: '1px solid #ddd', borderRadius: 1 }}>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                <Typography variant="subtitle1">
                  {ranking.username}
                </Typography>
                <Chip
                  label={ranking.ranking}
                  sx={{ 
                    backgroundColor: getRankingColor(ranking.ranking),
                    color: 'white'
                  }}
                />
              </Box>
              {ranking.comments && (
                <Typography variant="body2" color="text.secondary">
                  {ranking.comments}
                </Typography>
              )}
              <Typography variant="caption" color="text.secondary">
                {new Date(ranking.updated_at).toLocaleString()}
              </Typography>
            </Box>
          ))}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowRankingDetails(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ProjectRanking; 