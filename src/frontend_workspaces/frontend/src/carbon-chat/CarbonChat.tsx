/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */

import React, { useCallback, useRef } from 'react';
import {
  ChatCustomElement,
  type ChatInstance,
  type MessageRequest,
  type CustomSendMessageOptions,
  CarbonTheme,
  BusEventType,
} from '@carbon/ai-chat';
import { customSendMessage as customSendMessageImpl } from './customSendMessage';
import './CarbonChat.css';

// Reset thread ID when conversation restarts
let currentThreadId: string | null = null;

function resetThreadId() {
  currentThreadId = null;
}

export function getOrCreateThreadId(): string {
  if (!currentThreadId) {
    currentThreadId = crypto.randomUUID();
  }
  return currentThreadId;
}

interface CarbonChatProps {
  className?: string;
  theme?: 'light' | 'dark';
  contained?: boolean;
  useDraft?: boolean;
}

const CarbonChat = ({
  className = '',
  theme = 'light',
  contained = false,
  useDraft = false
}: CarbonChatProps) => {
  const chatInstanceRef = useRef<ChatInstance | null>(null);

  // Wrap the custom send message function to ensure it's properly bound
  const handleCustomSendMessage = useCallback(
    async (
      request: MessageRequest,
      options: CustomSendMessageOptions,
      instance: ChatInstance
    ) => {
      return await customSendMessageImpl(request, options, instance, useDraft);
    },
    [useDraft]
  );

  const handleChatReady = useCallback((instance: ChatInstance) => {
    chatInstanceRef.current = instance;
    instance.on({
      type: BusEventType.RESTART_CONVERSATION,
      handler: () => {
        resetThreadId();
      },
    });
  }, []);

  return (
    <ChatCustomElement
      className={`${contained ? 'carbon-chat-contained' : 'carbon-chat-fullscreen'} ${className}`}
      injectCarbonTheme={theme === 'dark' ? CarbonTheme.G100 : CarbonTheme.WHITE}
      openChatByDefault={true}
      assistantName="CUGA Agent"
      header={{
        isOn: true,
        showRestartButton: true,
        showCloseButton: false
      }}
      layout={{
        showFrame: false,
        hasContentMaxWidth: true,
      }}
      input={{
        isVisible: true,
      }}
      messaging={{
        customSendMessage: handleCustomSendMessage,
      }}
      onAfterRender={handleChatReady}
    />
  );
};

export default CarbonChat;