const EventEmitter = require('events');

class JobQueue extends EventEmitter {
  constructor() {
    super();
    this.jobs = new Map(); // jobId -> job details
    this.queue = []; // Array of job IDs waiting to be processed
    this.processing = false;
    this.currentJob = null;
    this.workers = 1; // Number of concurrent workers
    this.activeWorkers = 0;
  }

  // Create a new job
  createJob(type, data, options = {}) {
    const jobId = this.generateJobId();
    const job = {
      id: jobId,
      type,
      data,
      status: 'queued',
      progress: 0,
      total: data.tickets?.length || 1,
      results: [],
      errors: [],
      createdAt: new Date(),
      startedAt: null,
      completedAt: null,
      ...options
    };

    this.jobs.set(jobId, job);
    this.queue.push(jobId);
    
    this.emit('jobCreated', job);
    this.processQueue();
    
    return jobId;
  }

  // Get job status
  getJob(jobId) {
    return this.jobs.get(jobId);
  }

  // Get all jobs (for debugging)
  getAllJobs() {
    return Array.from(this.jobs.values());
  }

  // Update job progress
  updateJobProgress(jobId, progress, result = null, error = null) {
    const job = this.jobs.get(jobId);
    if (!job) return;

    job.progress = progress;
    
    if (result) {
      job.results.push(result);
    }
    
    if (error) {
      job.errors.push(error);
    }

    // Update status based on progress
    if (progress >= job.total) {
      job.status = 'completed';
      job.completedAt = new Date();
      this.emit('jobCompleted', job);
    } else if (job.status === 'queued') {
      job.status = 'processing';
      job.startedAt = new Date();
      this.emit('jobStarted', job);
    }

    this.emit('jobUpdated', job);
  }

  // Mark job as failed
  failJob(jobId, error) {
    const job = this.jobs.get(jobId);
    if (!job) return;

    job.status = 'failed';
    job.completedAt = new Date();
    job.errors.push(error);
    
    this.emit('jobFailed', job);
  }

  // Process the queue
  async processQueue() {
    if (this.activeWorkers >= this.workers || this.queue.length === 0) {
      return;
    }

    this.activeWorkers++;
    
    while (this.queue.length > 0 && this.activeWorkers <= this.workers) {
      const jobId = this.queue.shift();
      const job = this.jobs.get(jobId);
      
      if (!job || job.status !== 'queued') {
        continue;
      }

      this.currentJob = jobId;
      
      try {
        await this.processJob(job);
      } catch (error) {
        console.error(`Job ${jobId} failed:`, error.message);
        this.failJob(jobId, error.message);
      }
    }

    this.activeWorkers--;
    this.currentJob = null;
  }

  // Process a single job - to be overridden by specific implementations
  async processJob(job) {
    throw new Error('processJob must be implemented by subclass');
  }

  // Generate unique job ID
  generateJobId() {
    return `job_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  // Clean up completed jobs older than specified time
  cleanup(maxAge = 24 * 60 * 60 * 1000) { // 24 hours default
    const cutoff = new Date(Date.now() - maxAge);
    
    for (const [jobId, job] of this.jobs.entries()) {
      if (job.completedAt && job.completedAt < cutoff) {
        this.jobs.delete(jobId);
      }
    }
  }

  // Get queue statistics
  getStats() {
    const jobs = Array.from(this.jobs.values());
    return {
      total: jobs.length,
      queued: jobs.filter(j => j.status === 'queued').length,
      processing: jobs.filter(j => j.status === 'processing').length,
      completed: jobs.filter(j => j.status === 'completed').length,
      failed: jobs.filter(j => j.status === 'failed').length,
      activeWorkers: this.activeWorkers,
      currentJob: this.currentJob
    };
  }
}

module.exports = JobQueue; 