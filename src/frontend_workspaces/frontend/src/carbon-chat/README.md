# Carbon Chat Component

This folder contains the Carbon AI Chat component with reasoning steps support, upgraded to @carbon/ai-chat v1.6.0.

## Files

- **CarbonChat.tsx** - Main React component that initializes and renders the Carbon AI Chat interface
- **scenarios.ts** - Contains different reasoning scenarios (reasoning steps, controlled reasoning, reasoning content, chain of thought)
- **customSendMessage.ts** - Custom message handler that processes user input and triggers appropriate scenarios
- **index.tsx** - Barrel export file for easy imports

## Features

- **Reasoning Steps**: Auto-opens reasoning steps while the model provides them, then auto-opens the active step
- **Controlled Reasoning Steps**: Keeps all reasoning steps closed by default with a loading indicator
- **Reasoning Content**: Streams reasoning as a single trace without individual steps
- **Chain of Thought**: Best suited for raw debugging or tool-call traces

## Usage

The component is available at the `/chat` route in the application.

```tsx
import { CarbonChat } from './carbon-chat';

function MyComponent() {
  return <CarbonChat theme="light" />;
}
```

## Props

- `className` (optional): Additional CSS classes to apply to the container
- `theme` (optional): 'light' or 'dark' theme (default: 'light')

## Scenarios

Users can select from a dropdown menu to see different reasoning patterns:

1. **Reasoning steps** - Default behavior with auto-opening steps
2. **Controlled reasoning steps** - Closed steps with loading indicator
3. **Reasoning content** - Long-form rationale without expandable steps
4. **Chain of thought** - Raw debugging traces

Each scenario demonstrates how the UI responds to different mocked streaming patterns.