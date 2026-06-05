const STORAGE_KEY = "agent-wiki:theme";

const bootstrapSource = `(()=>{try{var k=${JSON.stringify(STORAGE_KEY)};var s=localStorage.getItem(k);if(s!=='light'&&s!=='dark'&&s!=='system')s='system';var r=s;if(s==='system')r=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';var el=document.documentElement;el.setAttribute('data-theme',r);el.classList.toggle('dark',r==='dark');}catch(e){}})();`;

export function ThemeBootstrapScript() {
  return <script dangerouslySetInnerHTML={{ __html: bootstrapSource }} />;
}
