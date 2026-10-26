import test from 'node:test';
import assert from 'node:assert/strict';
import { useSideLayout } from '../../web/src/layout-mode.ts';
const edge = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/130.0 Version/18.0 Mobile/15E148 Safari/604.1';
const x = 'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/24A437 Twitter for iPhone/12.26';
for (const h of [540,634,679]) {
 test(`同じ402×${h}でもEdgeは上下、Xは左右`,()=>{
  assert.equal(useSideLayout(402,h,false,edge),false);
  assert.equal(useSideLayout(402,h,false,x),true);
 });
}
test('通常の縦向きSafariは上下',()=>assert.equal(useSideLayout(375,600,false,'Mobile/15E148 Safari/604.1'),false));
test('広い高さと入力中はXでも上下',()=>{
 assert.equal(useSideLayout(402,790,false,x),false);
 assert.equal(useSideLayout(402,634,true,x),false);
});
test('横向きの低い画面は左右',()=>assert.equal(useSideLayout(874,402,false,edge),true));
