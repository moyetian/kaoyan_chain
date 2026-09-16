function fallbackMathUnicode(el){
  if(!el) return;
  function cleanLatex(s){
    return s
      .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)')
      .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
      .replace(/\\iint/g, '∬').replace(/\\int/g, '∫')
      .replace(/\\sum/g, '∑').replace(/\\prod/g, '∏').replace(/\\infty/g, '∞')
      .replace(/\\lim_?\{([^}]*)\}/g, 'lim($1)').replace(/\\lim/g, 'lim')
      .replace(/\\alpha/g, 'α').replace(/\\beta/g, 'β').replace(/\\gamma/g, 'γ')
      .replace(/\\delta/g, 'δ').replace(/\\epsilon/g, 'ε').replace(/\\theta/g, 'θ')
      .replace(/\\lambda/g, 'λ').replace(/\\pi/g, 'π').replace(/\\sigma/g, 'σ')
      .replace(/\\xi/g, 'ξ').replace(/\\eta/g, 'η').replace(/\\phi/g, 'φ')
      .replace(/\\le(q)?/g, '≤').replace(/\\ge(q)?/g, '≥').replace(/\\ne(q)?/g, '≠')
      .replace(/\\approx/g, '≈').replace(/\\pm/g, '±').replace(/\\times/g, '×')
      .replace(/\\cdot/g, '·').replace(/\\to/g, '→').replace(/\\rightarrow/g, '→')
      .replace(/\\in/g, '∈').replace(/\\subset/g, '⊂').replace(/\\cap/g, '∩').replace(/\\cup/g, '∪')
      .replace(/\^2/g, '²').replace(/\^3/g, '³').replace(/\^n/g, 'ⁿ')
      .replace(/_([0-9a-z])/g, '₍$1₎')
      .replace(/\\[a-zA-Z]+/g, '')
      .replace(/[{}]/g, '');
  }
  function walk(node){
    if(node.nodeType === 3){
      var txt = node.nodeValue;
      if(/\$|\\\(|\\\[/.test(txt)){
        var rep = txt.replace(/\$\$([\s\S]*?)\$\$/g, function(_, m){
          return '【 ' + cleanLatex(m) + ' 】';
        }).replace(/\$([\s\S]*?)\$/g, function(_, m){
          return cleanLatex(m);
        });
        if(rep !== txt) node.nodeValue = rep;
      }
    }else if(node.nodeType === 1 && node.nodeName !== 'SCRIPT' && node.nodeName !== 'STYLE'){
      for(var i=0; i<node.childNodes.length; i++){
        walk(node.childNodes[i]);
      }
    }
  }
  walk(el);
}
