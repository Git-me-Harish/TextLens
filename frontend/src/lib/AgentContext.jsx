import { createContext, useContext, useState } from "react";

const AgentContext = createContext(null);

export function AgentProvider({ children }) {
  // { domain, pipeline, label } while running, null when idle
  const [activeAgent, setActiveAgent] = useState(null);

  const startAgent = (domain, pipeline, label) => setActiveAgent({ domain, pipeline, label });
  const clearAgent = () => setActiveAgent(null);

  return (
    <AgentContext.Provider value={{ activeAgent, startAgent, clearAgent }}>
      {children}
    </AgentContext.Provider>
  );
}

export const useAgent = () => useContext(AgentContext);