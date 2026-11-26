import React from 'react';
import {preparationStatus} from './preparation-state.js';
const h=React.createElement;

export function PreparationRow({paper,onInspect}) {
  const status=preparationStatus(paper);
  return h('div',{className:'preparation-row'},
    h('div',{className:'preparation-paper'},h('strong',null,paper.title),h('small',null,paper.year || 'Year not provided')),
    h('div',{className:'preparation-access'},h('span',null,'Full text'),h('strong',null,status.availability)),
    h('div',{className:'preparation-process'},h('span',null,'Processing'),h('strong',null,status.processing),paper.document?.attempt?.outcome==='failed' && h('small',null,paper.document.message)),
    h('button',{type:'button',className:'secondary inspect-paper',onClick:onInspect,'aria-label':`Inspect details for ${paper.title}`},'Inspect details'));
}
