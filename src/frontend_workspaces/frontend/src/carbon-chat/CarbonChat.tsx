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
} from '@carbon/ai-chat';
import { customSendMessage as customSendMessageImpl } from './customSendMessage';
import './CarbonChat.css';

interface CarbonChatProps {
  className?: string;
  theme?: 'light' | 'dark';
}

const CarbonChat = ({
  className = '',
  theme = 'light'
}: CarbonChatProps) => {
  const chatInstanceRef = useRef<ChatInstance | null>(null);

  // Wrap the custom send message function to ensure it's properly bound
  const handleCustomSendMessage = useCallback(
    async (
      request: MessageRequest,
      options: CustomSendMessageOptions,
      instance: ChatInstance
    ) => {
      return await customSendMessageImpl(request, options, instance);
    },
    []
  );

  // Callback to get the chat instance when it's ready
  const handleChatReady = useCallback((instance: ChatInstance) => {
    chatInstanceRef.current = instance;
    
    // Send initial welcome message
    customSendMessageImpl(
      { input: { text: '' } },
      { signal: new AbortController().signal, silent: false },
      instance
    );
  }, []);

  return (
    <ChatCustomElement
      className={`carbon-chat-fullscreen ${className}`}
      injectCarbonTheme={theme === 'dark' ? CarbonTheme.G100 : CarbonTheme.WHITE}
      openChatByDefault={true}
      assistantName="CUGA Agent"
      header={{
        isOn: true,
        showRestartButton: true
      }}
      layout={{
        showFrame: false,
        hasContentMaxWidth: true,
      }}
      messaging={{
        customSendMessage: handleCustomSendMessage,
      }}
      onAfterRender={handleChatReady}
    />
  );
};

export default CarbonChat;