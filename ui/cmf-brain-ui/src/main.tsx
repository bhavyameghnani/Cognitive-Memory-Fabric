import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { DataSourcePage } from './components/DataSourcePage.tsx';
import { ArchitecturePage } from './components/ArchitecturePage.tsx';
import './index.css';

// Hash routing keeps this a single-page app with no router dependency: #/data is "Learn about the data source", #/architecture is "Design & architecture".
function Root() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => { const f = () => { setHash(window.location.hash); window.scrollTo(0, 0); }; window.addEventListener('hashchange', f); return () => window.removeEventListener('hashchange', f); }, []);
  if (hash === '#/data') return <DataSourcePage />;
  if (hash === '#/architecture') return <ArchitecturePage />;
  return <App />;
}

createRoot(document.getElementById('root')!).render(<Root />);
