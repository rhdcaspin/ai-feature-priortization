import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
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
  Alert,
  CircularProgress,
  Chip,
  LinearProgress
} from '@mui/material';
import { 
  Folder as ProjectIcon,
  Assessment as AssessmentIcon,
  People as PeopleIcon,
  Sync as SyncIcon 
} from '@mui/icons-material';
import { apiService } from '../services/api';

const Dashboard = () => {
  const navigate = useNavigate();
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [selectedVersion, setSelectedVersion] = useState('');
  const [versions, setVersions] = useState([]);
  const [projectStats, setProjectStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncingJira, setSyncingJira] = useState(false);
  const [error, setError] = useState('');

  // Define syncFromJira first since loadProjects depends on it
  const syncFromJira = useCallback(async () => {
    try {
      setSyncingJira(true);
      setError('');
      
      // Test Jira connection first
      await apiService.jira.test();
      
      // Sync projects from Jira
      const response = await apiService.jira.getProjects();
      setProjects(response.data);
      
      // Set ROX as default project if it exists after sync
      const roxProject = response.data.find(p => p.jira_key === 'ROX' || p.key === 'ROX');
      if (roxProject && !selectedProject) {
        setSelectedProject(roxProject.jira_key || roxProject.key);
      }
    } catch (error) {
      setError('Failed to sync from Jira: ' + (error.response?.data?.error || error.message));
    } finally {
      setSyncingJira(false);
    }
  }, [selectedProject]);

  const loadProjects = useCallback(async () => {
    try {
      setLoading(true);
      const response = await apiService.projects.getAll();
      setProjects(response.data);
      
      // Set ROX as default project if it exists
      const roxProject = response.data.find(p => p.jira_key === 'ROX' || p.key === 'ROX');
      if (roxProject && !selectedProject) {
        setSelectedProject(roxProject.jira_key || roxProject.key);
      }
      
      // If no projects, try to sync from Jira
      if (response.data.length === 0) {
        await syncFromJira();
      }
    } catch (error) {
      setError('Failed to load projects: ' + (error.response?.data?.error || error.message));
    } finally {
      setLoading(false);
    }
  }, [selectedProject, syncFromJira]);

  const loadVersions = useCallback(async () => {
    try {
      const response = await apiService.projects.getVersions(selectedProject);
      setVersions(response.data);
      setSelectedVersion(''); // Reset version selection
    } catch (error) {
      console.error('Failed to load versions:', error);
    }
  }, [selectedProject]);

  const loadProjectStats = useCallback(async () => {
    try {
      const response = await apiService.rankings.getProjectStats(selectedProject);
      setProjectStats(response.data);
    } catch (error) {
      console.error('Failed to load project stats:', error);
    }
  }, [selectedProject]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    if (selectedProject) {
      loadVersions();
      loadProjectStats();
    }
  }, [selectedProject, loadVersions, loadProjectStats]);

  const handleProjectSelect = (event) => {
    setSelectedProject(event.target.value);
  };

  const handleVersionSelect = (event) => {
    setSelectedVersion(event.target.value);
  };

  const handleStartRanking = () => {
    if (selectedProject && selectedVersion) {
      navigate(`/project/${selectedProject}?version=${selectedVersion}`);
    } else {
      navigate(`/project/${selectedProject}`);
    }
  };

  const handleSyncProject = async () => {
    if (!selectedProject) return;
    
    try {
      setSyncingJira(true);
      await apiService.jira.getProjectIssues(selectedProject, {
        targetVersion: selectedVersion || undefined
      });
      
      // Reload project stats
      await loadProjectStats();
    } catch (error) {
      setError('Failed to sync project tickets: ' + (error.response?.data?.error || error.message));
    } finally {
      setSyncingJira(false);
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
      <Typography variant="h4" component="h1" gutterBottom>
        Project Dashboard
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {/* Project Selector */}
      <Card className="project-selector" sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Select Project and Version
          </Typography>
          
          <Grid container spacing={3} alignItems="center">
            <Grid item xs={12} md={4}>
              <FormControl fullWidth>
                <InputLabel>Project</InputLabel>
                <Select
                  value={selectedProject}
                  onChange={handleProjectSelect}
                  label="Project"
                >
                  {projects.map((project) => (
                    <MenuItem key={project.jira_key || project.key} value={project.jira_key || project.key}>
                      {project.name} ({project.jira_key || project.key})
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12} md={4}>
              <FormControl fullWidth disabled={!selectedProject}>
                <InputLabel>Target Version</InputLabel>
                <Select
                  value={selectedVersion}
                  onChange={handleVersionSelect}
                  label="Target Version"
                >
                  <MenuItem value="">All Versions</MenuItem>
                  {versions.map((version) => (
                    <MenuItem key={version} value={version}>
                      {version}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12} md={4}>
              <Box display="flex" gap={1}>
                <Button
                  variant="contained"
                  onClick={handleStartRanking}
                  disabled={!selectedProject}
                  startIcon={<AssessmentIcon />}
                  sx={{ mr: 1 }}
                >
                  Start Ranking
                </Button>
                <Button
                  variant="outlined"
                  onClick={handleSyncProject}
                  disabled={!selectedProject || syncingJira}
                  startIcon={syncingJira ? <CircularProgress size={20} /> : <SyncIcon />}
                >
                  Sync
                </Button>
              </Box>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {/* Project Stats */}
      {selectedProject && projectStats && (
        <Grid container spacing={3}>
          <Grid item xs={12} md={3}>
            <Card className="stats-card">
              <CardContent>
                <Box display="flex" alignItems="center" gap={2}>
                  <ProjectIcon color="primary" />
                  <Box>
                    <Typography variant="h4">{projectStats.total_tickets || 0}</Typography>
                    <Typography color="textSecondary">Total Tickets</Typography>
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} md={3}>
            <Card className="stats-card">
              <CardContent>
                <Box display="flex" alignItems="center" gap={2}>
                  <PeopleIcon color="primary" />
                  <Box>
                    <Typography variant="h4">{projectStats.participating_users || 0}</Typography>
                    <Typography color="textSecondary">Participating Users</Typography>
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} md={3}>
            <Card className="stats-card">
              <CardContent>
                <Box display="flex" alignItems="center" gap={2}>
                  <AssessmentIcon color="primary" />
                  <Box>
                    <Typography variant="h4">{projectStats.total_rankings || 0}</Typography>
                    <Typography color="textSecondary">Total Rankings</Typography>
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} md={3}>
            <Card className="stats-card">
              <CardContent>
                <Box display="flex" alignItems="center" gap={2}>
                  <Box sx={{ width: '100%' }}>
                    <Typography variant="h4">
                      {projectStats.avg_overall_score ? projectStats.avg_overall_score.toFixed(1) : '0.0'}
                    </Typography>
                    <Typography color="textSecondary">Average Score</Typography>
                    <LinearProgress 
                      variant="determinate" 
                      value={(projectStats.avg_overall_score || 0) * 25} 
                      sx={{ mt: 1 }}
                    />
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>

          {/* Ranking Distribution */}
          {projectStats.rankingDistribution && (
            <Grid item xs={12}>
              <Card>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Ranking Distribution
                  </Typography>
                  <Box display="flex" gap={2} flexWrap="wrap">
                    {projectStats.rankingDistribution.map((item) => (
                      <Chip
                        key={item.ranking}
                        label={`${item.ranking}: ${item.count}`}
                        className={`priority-${item.ranking.toLowerCase().replace(' ', '-').replace("'", '')}`}
                        variant="filled"
                      />
                    ))}
                  </Box>
                </CardContent>
              </Card>
            </Grid>
          )}
        </Grid>
      )}

      {/* No Projects Message */}
      {projects.length === 0 && !syncingJira && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              No Projects Found
            </Typography>
            <Typography color="textSecondary" paragraph>
              No projects have been loaded from Jira yet. Click the button below to sync projects from your Jira instance.
            </Typography>
            <Button
              variant="contained"
              onClick={syncFromJira}
              startIcon={<SyncIcon />}
            >
              Sync Projects from Jira
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Syncing Indicator */}
      {syncingJira && (
        <Card>
          <CardContent>
            <Box display="flex" alignItems="center" gap={2}>
              <CircularProgress size={24} />
              <Typography>Syncing with Jira...</Typography>
            </Box>
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

export default Dashboard; 