import React, { useState, useEffect, useCallback } from 'react';
import {
  Typography,
  Box,
  Card,
  CardContent,
  Grid,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Alert,
  CircularProgress,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormControl,
  InputLabel,
  Select,
  MenuItem
} from '@mui/material';
import {
  Update as UpdateIcon,
  People as PeopleIcon,
  Assessment as AssessmentIcon,
  Sync as SyncIcon,
  Download as ExportIcon
} from '@mui/icons-material';
import { apiService } from '../services/api';

const AdminDashboard = () => {
  const [projects, setProjects] = useState([]);
  const [users, setUsers] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [selectedVersion, setSelectedVersion] = useState('');
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [showBulkUpdateDialog, setShowBulkUpdateDialog] = useState(false);
  const [updateResults, setUpdateResults] = useState(null);

  const loadAdminData = useCallback(async () => {
    try {
      setLoading(true);
      const [projectsResponse, usersResponse] = await Promise.all([
        apiService.projects.getAll(),
        apiService.auth.users()
      ]);
      
      setProjects(projectsResponse.data);
      setUsers(usersResponse.data);
    } catch (error) {
      setError('Failed to load admin data: ' + (error.response?.data?.error || error.message));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadVersions = useCallback(async () => {
    try {
      const response = await apiService.projects.getVersions(selectedProject);
      setVersions(response.data);
      setSelectedVersion('');
    } catch (error) {
      console.error('Failed to load versions:', error);
    }
  }, [selectedProject]);

  useEffect(() => {
    loadAdminData();
  }, [loadAdminData]);

  useEffect(() => {
    if (selectedProject) {
      loadVersions();
    }
  }, [selectedProject, loadVersions]);

  const handleSyncAllProjects = async () => {
    try {
      setSyncing(true);
      setError('');
      setSuccess('');

      await apiService.jira.getProjects();
      await loadAdminData();
      
      setSuccess('Successfully synced all projects from Jira');
    } catch (error) {
      setError('Failed to sync projects: ' + (error.response?.data?.error || error.message));
    } finally {
      setSyncing(false);
    }
  };

  const handleBulkUpdateRanks = async () => {
    if (!selectedProject) return;

    try {
      setUpdating(true);
      setError('');
      setSuccess('');

      const response = await apiService.jira.bulkUpdateRanks(selectedProject, selectedVersion || undefined);
      setUpdateResults(response.data.results);
      setShowBulkUpdateDialog(false);
      
      setSuccess(`Successfully updated ranks for ${response.data.results.filter(r => r.success).length} tickets`);
    } catch (error) {
      setError('Failed to update Jira ranks: ' + (error.response?.data?.error || error.message));
    } finally {
      setUpdating(false);
    }
  };

  const handleExportProject = (projectKey) => {
    window.open(`/api/projects/${projectKey}/export`, '_blank');
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'Never';
    return new Date(dateString).toLocaleDateString();
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
        Admin Dashboard
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 3 }}>
          {success}
        </Alert>
      )}

      {/* Quick Stats */}
      <Grid container spacing={3} sx={{ mb: 4 }}>
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" gap={2}>
                <AssessmentIcon color="primary" />
                <Box>
                  <Typography variant="h4">{projects.length}</Typography>
                  <Typography color="textSecondary">Total Projects</Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" gap={2}>
                <PeopleIcon color="primary" />
                <Box>
                  <Typography variant="h4">{users.length}</Typography>
                  <Typography color="textSecondary">Registered Users</Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" gap={2}>
                <UpdateIcon color="primary" />
                <Box>
                  <Typography variant="h4">
                    {projects.reduce((sum, p) => sum + (p.ticket_count || 0), 0)}
                  </Typography>
                  <Typography color="textSecondary">Total Tickets</Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Actions */}
      <Grid container spacing={3} sx={{ mb: 4 }}>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Jira Integration
              </Typography>
              <Box display="flex" gap={2}>
                <Button
                  variant="contained"
                  onClick={handleSyncAllProjects}
                  disabled={syncing}
                  startIcon={syncing ? <CircularProgress size={20} /> : <SyncIcon />}
                >
                  Sync All Projects
                </Button>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Bulk Update Jira Ranks
              </Typography>
              <Box display="flex" gap={2} alignItems="center">
                <FormControl size="small" sx={{ minWidth: 150 }}>
                  <InputLabel>Project</InputLabel>
                  <Select
                    value={selectedProject}
                    onChange={(e) => setSelectedProject(e.target.value)}
                    label="Project"
                  >
                    {projects.map(project => (
                      <MenuItem key={project.jira_key} value={project.jira_key}>
                        {project.jira_key}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <FormControl size="small" sx={{ minWidth: 150 }} disabled={!selectedProject}>
                  <InputLabel>Version</InputLabel>
                  <Select
                    value={selectedVersion}
                    onChange={(e) => setSelectedVersion(e.target.value)}
                    label="Version"
                  >
                    <MenuItem value="">All Versions</MenuItem>
                    {versions.map(version => (
                      <MenuItem key={version} value={version}>
                        {version}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <Button
                  variant="contained"
                  onClick={() => setShowBulkUpdateDialog(true)}
                  disabled={!selectedProject || updating}
                  startIcon={updating ? <CircularProgress size={20} /> : <UpdateIcon />}
                  color="warning"
                >
                  Update Ranks
                </Button>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Projects Table */}
      <Card sx={{ mb: 4 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Projects Overview
          </Typography>
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Project Key</TableCell>
                  <TableCell>Name</TableCell>
                  <TableCell>Tickets</TableCell>
                  <TableCell>Participating Users</TableCell>
                  <TableCell>Last Updated</TableCell>
                  <TableCell>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {projects.map(project => (
                  <TableRow key={project.jira_key}>
                    <TableCell>
                      <Typography variant="subtitle2">
                        {project.jira_key}
                      </Typography>
                    </TableCell>
                    <TableCell>{project.name}</TableCell>
                    <TableCell>
                      <Chip 
                        label={project.ticket_count || 0} 
                        size="small" 
                        color="primary" 
                      />
                    </TableCell>
                    <TableCell>
                      <Chip 
                        label={project.participating_users || 0} 
                        size="small" 
                        color="secondary" 
                      />
                    </TableCell>
                    <TableCell>{formatDate(project.updated_at)}</TableCell>
                    <TableCell>
                      <Button
                        size="small"
                        onClick={() => handleExportProject(project.jira_key)}
                        startIcon={<ExportIcon />}
                      >
                        Export
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* Users Table */}
      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Registered Users
          </Typography>
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Username</TableCell>
                  <TableCell>Email</TableCell>
                  <TableCell>Role</TableCell>
                  <TableCell>Registered</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {users.map(user => (
                  <TableRow key={user.id}>
                    <TableCell>
                      <Typography variant="subtitle2">
                        {user.username}
                      </Typography>
                    </TableCell>
                    <TableCell>{user.email}</TableCell>
                    <TableCell>
                      <Chip
                        label={user.role}
                        size="small"
                        color={user.role === 'admin' ? 'error' : 'default'}
                      />
                    </TableCell>
                    <TableCell>{formatDate(user.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* Bulk Update Confirmation Dialog */}
      <Dialog open={showBulkUpdateDialog} onClose={() => setShowBulkUpdateDialog(false)}>
        <DialogTitle>Confirm Bulk Update</DialogTitle>
        <DialogContent>
          <Typography paragraph>
            This will update the rank field in Jira for all tickets in project "{selectedProject}"
            {selectedVersion ? ` for version "${selectedVersion}"` : ' (all versions)'}
            based on the aggregated team rankings.
          </Typography>
          <Typography paragraph color="warning.main">
            <strong>Warning:</strong> This action will modify data in Jira and cannot be undone easily.
          </Typography>
          <Typography>
            Are you sure you want to proceed?
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowBulkUpdateDialog(false)}>
            Cancel
          </Button>
          <Button 
            onClick={handleBulkUpdateRanks} 
            color="warning" 
            variant="contained"
            disabled={updating}
            startIcon={updating ? <CircularProgress size={20} /> : <UpdateIcon />}
          >
            Update Jira Ranks
          </Button>
        </DialogActions>
      </Dialog>

      {/* Update Results */}
      {updateResults && (
        <Dialog 
          open={Boolean(updateResults)} 
          onClose={() => setUpdateResults(null)}
          maxWidth="md"
          fullWidth
        >
          <DialogTitle>Bulk Update Results</DialogTitle>
          <DialogContent>
            <Typography paragraph>
              Updated {updateResults.filter(r => r.success).length} of {updateResults.length} tickets.
            </Typography>
            
            <TableContainer component={Paper}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Ticket</TableCell>
                    <TableCell>New Rank</TableCell>
                    <TableCell>Score</TableCell>
                    <TableCell>Votes</TableCell>
                    <TableCell>Status</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {updateResults.map((result, index) => (
                    <TableRow key={index}>
                      <TableCell>{result.key}</TableCell>
                      <TableCell>{result.newRank}</TableCell>
                      <TableCell>{result.avgScore?.toFixed(2)}</TableCell>
                      <TableCell>{result.voteCount}</TableCell>
                      <TableCell>
                        <Chip
                          label={result.success ? 'Success' : 'Failed'}
                          color={result.success ? 'success' : 'error'}
                          size="small"
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setUpdateResults(null)}>Close</Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
};

export default AdminDashboard; 