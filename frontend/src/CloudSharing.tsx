import React from 'react';
import {CloudSync} from './CloudSync';

export function CloudSharing() {
  return <section className="sharing-page cloud-sharing" aria-labelledby="icloud-title">
    <a className="sharing-back" href="#/">← Your Stacks</a>
    <div className="intro">
      <p className="eyebrow">ICLOUD SHARING</p>
      <h1 id="icloud-title">Share by iCloud</h1>
      <p>Send articles and PDFs from anywhere, and receive completed audio through your personal iCloud Drive.</p>
    </div>
    <CloudSync/>
  </section>;
}
