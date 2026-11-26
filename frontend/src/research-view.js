import React from 'react';
import { displayAuthors } from './api.js';

export function PaperSummary({ paper }) {
  return React.createElement(React.Fragment, null,
    React.createElement('h3', null, paper.title),
    React.createElement('p', null, displayAuthors(paper.authors)));
}
