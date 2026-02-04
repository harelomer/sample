# WhatsApp Chatbot

A simple WhatsApp chatbot built with Node.js and Green-API.

## Features

- Cloud-based WhatsApp API (no browser required)
- Auto-replies for common greetings
- Bot commands with `!` prefix
- Easy setup via Green-API console
- Graceful shutdown handling

## Available Commands

| Command | Description |
|---------|-------------|
| `!help` | Show available commands |
| `!ping` | Check if bot is online |
| `!info` | Get information about the bot |
| `!echo <message>` | Echo back your message |
| `!time` | Get current server time |
| `!joke` | Get a random programming joke |

## Prerequisites

- Node.js 16 or higher
- npm or yarn
- A WhatsApp account
- Green-API account (free tier available)

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd whatsapp-chatbot
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Set up Green-API:
   - Go to https://green-api.com/ and create an account
   - Create a new instance in the console
   - Scan the QR code with WhatsApp to link your account
   - Copy your `idInstance` and `apiTokenInstance`

4. Configure environment variables:
   ```bash
   cp .env.example .env
   ```

   Edit `.env` and add your Green-API credentials:
   ```
   GREEN_API_ID_INSTANCE=your_id_instance
   GREEN_API_TOKEN_INSTANCE=your_api_token_instance
   ```

5. Start the bot:
   ```bash
   npm start
   ```

## Development

Run with auto-reload on file changes:

```bash
npm run dev
```

## Project Structure

```
whatsapp-chatbot/
├── src/
│   ├── index.js              # Main entry point
│   └── handlers/
│       └── messageHandler.js # Message processing logic
├── .env.example              # Environment variables template
├── .gitignore               # Git ignore rules
├── package.json             # Project configuration
└── README.md                # This file
```

## Customization

### Adding New Commands

Edit `src/handlers/messageHandler.js`:

1. Add the command to the `commands` object:
   ```javascript
   const commands = {
       // ... existing commands
       mycommand: {
           description: 'My custom command',
           usage: '!mycommand'
       }
   };
   ```

2. Add a case in the `handleCommand` switch statement:
   ```javascript
   case 'mycommand':
       await sendMessage(restAPI, chatId, 'This is my custom command response!');
       break;
   ```

### Modifying Auto-Replies

Edit the `handleAutoReply` function in `src/handlers/messageHandler.js` to add or modify automatic responses.

## Green-API Setup Guide

1. Visit https://console.green-api.com/
2. Register or log in to your account
3. Click "Create Instance"
4. Scan the QR code with WhatsApp (Settings > Linked Devices > Link a Device)
5. Once linked, copy your credentials from the instance dashboard
6. Paste them into your `.env` file

## Troubleshooting

### Instance Not Authorized
- Go to https://console.green-api.com/
- Check if your instance shows as "authorized"
- If not, scan the QR code again

### Bot Not Receiving Messages
- Verify your credentials are correct in `.env`
- Check if the instance is active in Green-API console
- Ensure you're sending messages to the linked WhatsApp number

### Connection Errors
- Check your internet connection
- Verify Green-API service status
- Ensure your API credentials haven't expired

## License

MIT
