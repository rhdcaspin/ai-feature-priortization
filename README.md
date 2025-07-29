# Jira Feature Prioritization Tool

A collaborative tool for teams to prioritize Jira features using the MoSCoW method (Must have, Should have, Could have, Won't have) with team consensus and automated Jira rank updates.

## Features

- **Jira Integration**: Connects to Red Hat Jira instance to fetch projects, versions, and tickets
- **Custom Field Support**: Uses Jira custom field `customfield_12319940` for target version filtering
- **MoSCoW Prioritization**: Team members rank features using the established MoSCoW methodology
- **Team Collaboration**: Multiple team members can submit rankings for the same features
- **Consensus Tracking**: Shows team consensus levels and vote distribution
- **Automated Ranking**: Aggregates team votes and updates Jira ticket ranks automatically
- **Real-time Results**: View aggregated team rankings with detailed analytics
- **Admin Dashboard**: Administrative tools for managing projects and bulk updates
- **Export Functionality**: Export ranking results to CSV format

## Technology Stack

### Backend
- **Node.js** with Express.js
- **SQLite** database for storing rankings and user data
- **JWT** authentication
- **Axios** for Jira API integration
- **bcrypt** for password hashing

### Frontend
- **React** with React Router
- **Material-UI** for modern, responsive design
- **Axios** for API communication
- **Context API** for state management

## Project Structure

```
jira-feature-prioritization/
├── server.js                 # Main Express server
├── package.json              # Backend dependencies
├── database/
│   └── init.js               # Database initialization and helpers
├── routes/
│   ├── auth.js               # Authentication routes
│   ├── jira.js               # Jira API integration routes
│   ├── projects.js           # Project management routes
│   └── ranking.js            # Ranking submission and aggregation routes
└── client/                   # React frontend
    ├── package.json          # Frontend dependencies
    ├── public/
    │   └── index.html
    └── src/
        ├── App.js            # Main App component with routing
        ├── index.js          # React entry point
        ├── services/
        │   └── api.js        # API service layer
        └── components/
            ├── Login.js      # Authentication component
            ├── Dashboard.js  # Project selection dashboard
            ├── ProjectRanking.js  # Main ranking interface
            └── AdminDashboard.js  # Admin management interface
```

## Setup Instructions

### Prerequisites
- Node.js (v14 or higher)
- npm or yarn
- Access to Red Hat Jira instance with a valid API token

### Installation

1. **Clone and setup the project:**
   ```bash
   cd aifeaturepriortization
   npm install
   cd client
   npm install
   cd ..
   ```

2. **Configure environment variables:**
   Create a `.env` file in the root directory:
   ```env
   JIRA_BASE_URL=https://issues.redhat.com
   JIRA_TOKEN=your_jira_api_token_here
   JWT_SECRET=your_jwt_secret_here
   PORT=3001
   DATABASE_PATH=./database.sqlite
   ```

   **Note**: The application uses Jira custom field `customfield_12319940` as the target version field. Ensure your Jira tickets have this field populated for proper version filtering.

3. **Start the development servers:**
   
   **Option 1: Run both servers concurrently**
   ```bash
   npm run dev:concurrent
   ```

   **Option 2: Run separately**
   ```bash
   # Terminal 1 - Backend
   npm run dev

   # Terminal 2 - Frontend
   npm run client
   ```

4. **Access the application:**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:3001

## Usage Guide

### Initial Setup

1. **Create Admin User**: On first launch, register a user and then use the setup-admin endpoint:
   ```bash
   curl -X POST http://localhost:3001/api/auth/setup-admin \
     -H "Content-Type: application/json" \
     -d '{"username":"admin","email":"admin@example.com","password":"your_password"}'
   ```

2. **Sync Projects**: Login as admin and use the "Sync Projects from Jira" button to import projects.

### Team Member Workflow

1. **Register/Login**: Create an account or login with existing credentials
2. **Select Project**: Choose a project and optionally a target version
3. **Rank Features**: Use the MoSCoW method to rank each feature:
   - **Must have**: Critical features required for success
   - **Should have**: Important features that add significant value
   - **Could have**: Nice-to-have features if time/resources permit
   - **Won't have**: Features that won't be included this time
4. **Add Comments**: Optionally add reasoning for your rankings
5. **View Results**: Switch to "Team Results" to see aggregated rankings

### Admin Functions

1. **Project Management**: Sync projects and tickets from Jira
2. **User Management**: View registered users and their activity
3. **Bulk Updates**: Apply aggregated rankings back to Jira ticket ranks
4. **Export Data**: Download ranking results as CSV files

## API Endpoints

### Authentication
- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - User login
- `GET /api/auth/me` - Get current user info
- `POST /api/auth/setup-admin` - Create admin user (one-time)

### Jira Integration
- `GET /api/jira/test` - Test Jira connection
- `GET /api/jira/projects` - Fetch all projects from Jira
- `GET /api/jira/projects/:projectKey/versions` - Get project versions
- `GET /api/jira/projects/:projectKey/issues` - Get project tickets
- `POST /api/jira/bulk-update-ranks` - Update Jira ranks based on rankings

### Rankings
- `POST /api/ranking/rank` - Submit a feature ranking
- `GET /api/ranking/project/:projectKey/version/:version/summary` - Get aggregated rankings
- `GET /api/ranking/user/:userId/project/:projectKey/version/:version` - Get user's rankings
- `GET /api/ranking/project/:projectKey/stats` - Get project statistics

### Projects
- `GET /api/projects` - Get all projects
- `GET /api/projects/:projectKey` - Get specific project details
- `GET /api/projects/:projectKey/export` - Export rankings as CSV

## Database Schema

### Users Table
- `id`: Primary key
- `username`: Unique username
- `email`: User email address
- `password_hash`: Hashed password
- `role`: User role (admin/member)
- `created_at`: Registration timestamp

### Projects Table
- `id`: Primary key
- `jira_key`: Jira project key
- `name`: Project name
- `description`: Project description
- `target_version`: Current target version
- `created_at/updated_at`: Timestamps

### Tickets Table
- `id`: Primary key
- `jira_key`: Jira ticket key
- `project_id`: Foreign key to projects
- `title`: Ticket title
- `description`: Ticket description
- `issue_type`: Type of issue
- `status`: Current status
- `priority`: Jira priority
- `target_version`: Target version
- `jira_rank`: Current Jira rank
- `current_rank`: Calculated rank from rankings

### Rankings Table
- `id`: Primary key
- `user_id`: Foreign key to users
- `ticket_id`: Foreign key to tickets
- `ranking`: MoSCoW ranking value
- `comments`: Optional comments
- `created_at/updated_at`: Timestamps

## MoSCoW Prioritization Method

The tool implements the MoSCoW prioritization technique:

- **Must have (M)**: Critical requirements that must be satisfied for the project to be considered successful
- **Should have (S)**: Important requirements that should be included if possible, but the project can still succeed without them
- **Could have (C)**: Desirable requirements that would be nice to have but are not critical
- **Won't have (W)**: Requirements that have been agreed will not be implemented in the current iteration

### Scoring System
- Must have: 4 points
- Should have: 3 points
- Could have: 2 points
- Won't have: 1 point

The system calculates average scores and uses them to rank features automatically.

## Consensus Tracking

The tool tracks team consensus levels:

- **Strong consensus** (75%+): Most team members agree on the ranking
- **Moderate consensus** (50-74%): Majority agreement with some disagreement
- **No consensus** (<50%): Significant disagreement in rankings

## Security Features

- JWT-based authentication
- Password hashing with bcrypt
- Role-based access control (admin/member)
- API request validation
- Secure Jira token handling

## Development

### Running Tests
```bash
npm test
```

### Building for Production
```bash
npm run build
```

### Environment Variables
- `JIRA_BASE_URL`: Base URL for Jira instance
- `JIRA_TOKEN`: API token for Jira authentication
- `JWT_SECRET`: Secret key for JWT tokens
- `PORT`: Server port (default: 3001)
- `DATABASE_PATH`: SQLite database file path
- `NODE_ENV`: Environment (development/production)

## Troubleshooting

### Common Issues

1. **Jira Connection Failed**
   - Verify the JIRA_BASE_URL and JIRA_TOKEN are correct
   - Check network connectivity to Jira instance
   - Ensure token has appropriate permissions

2. **Target Version Issues**
   - Ensure custom field `customfield_12319940` exists in your Jira instance
   - Verify tickets have this field populated with target version values
   - Check that the Jira token has permission to read custom fields

3. **Database Errors**
   - Check DATABASE_PATH is writable
   - Ensure SQLite is properly installed
   - Check disk space availability

4. **Authentication Issues**
   - Verify JWT_SECRET is set
   - Check token expiration (24h default)
   - Ensure proper CORS configuration

### Logs
Server logs are output to console. In production, consider using a logging service.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

MIT License - see LICENSE file for details

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review server logs for error details
3. Ensure all environment variables are properly configured
4. Verify Jira connectivity and permissions 