"""Published data shapes for the real public-source parsers; no real account/network."""
import json

NAMES=['Claude Opus 5 (high)','Claude Opus 5 (low)','Gemini 3.5 Flash','Qwen 3 235B','Alpha 1','Beta 2']
ORGS=['Anthropic','Anthropic','Google DeepMind','Alibaba','Example','Example']
def rsc(payload):
    return '<script>self.__next_f.push([1,'+json.dumps(json.dumps(payload,separators=(',',':')))+'])</script>'

def catalog_models():
    return {'data':[{'id':identity,'name':org+': '+name,'description':'Published capabilities and practical model details. '*5,
                    'context_length':200000,'supported_parameters':['tools','reasoning'],
                    'architecture':{'input_modalities':['text','image'],'output_modalities':['text']},
                    'pricing':{'prompt':'0.000001','completion':'0.000003'}}
                   for identity,org,name in [('anthropic/claude-opus-5','Anthropic','Claude Opus 5'),
                                             ('google/gemini-3.5-flash','Google','Gemini 3.5 Flash'),
                                             ('qwen/qwen-3-235b','Qwen','Qwen 3 235B'),
                                             ('future/unknown','Future','Unknown model')]]}

def source_fixtures():
    out={'https://openrouter.ai/api/v1/models':json.dumps(catalog_models()),'https://arena.ai/leaderboard':'<a href="/leaderboard/text">Text</a><a href="/leaderboard/code/webdev">Code</a><a href="/leaderboard/vision">Vision</a>'}
    out['https://models.dev/api.json?type=all']=json.dumps({'extra':{'name':'Extra provider','models':{
        'additional':{'id':'extra/additional','canonical_model_id':'extra/additional','name':'Additional API model','cost':{'input':2,'output':5},'limit':{'context':128000,'output':32000},'modalities':{'input':['text'],'output':['text']}},
        'overlap':{'id':'google/gemini-3.5-flash','canonical_model_id':'google/gemini-3.5-flash','name':'Duplicate Gemini','cost':{'input':99,'output':99}}}}})
    arena=[{'modelDisplayName':name,'modelOrganization':org,'rating':rating,'rank':i+1,'votes':500} for i,(name,org,rating) in enumerate(zip(NAMES,ORGS,[1600,1400,1550,1300,1200,1100]))]
    for slug in ['text','code/webdev','vision']:
        out['https://arena.ai/leaderboard/'+slug]=rsc({'leaderboard':{'arenaSlug':slug,'entries':arena},'arena':{'slug':slug,'title':slug,'description':'Published fixture'}})
    aa=[{'slug':name.lower().replace(' ','-'),'name':name,'modelCreatorName':org,'intelligenceIndex':score,'gpqa':score/100,'hle':score/300,'medianTimeToFirstTokenSeconds':latency}
        for name,org,score,latency in zip(NAMES,ORGS,[90,70,80,60,50,40],[1.2,.3,.5,.8,2,3])]
    out['https://artificialanalysis.ai/leaderboards/models']=rsc({'models':aa})
    out['https://livebench.ai/']='<script src="static/js/main.abc123.js"></script>'
    live_names=''.join('"'+name.lower().replace(' ','-')+'":{url:"https://example.com",organization:"'+org+'",displayName:"'+name+'"};' for name,org in zip(NAMES,ORGS))
    out['https://livebench.ai/static/js/main.abc123.js']='["2026-09-01","2026-08-01"];'+live_names
    out['https://livebench.ai/table_2026_09_01.csv']='model,coding,reasoning\n'+'\n'.join(name.lower().replace(' ','-')+','+str(90-i*8)+','+str(85-i*6) for i,name in enumerate(NAMES))
    out['https://livebench.ai/categories_2026_09_01.json']=json.dumps({'Coding':['coding'],'Reasoning':['reasoning']})
    out['https://simple-bench.com/static/js/leaderboard-data.js']='const rows=['+','.join('{rank:"'+str(i+1)+'",model:"'+name+'",score:"'+str(90-i*10)+'",organization:"'+org+'",dateAdded:"2026-09-01"}' for i,(name,org) in enumerate(zip(NAMES,ORGS)))+'];'
    out['https://eqbench.com/']='<script src="eqbench4/eqbench4_data.js"></script>'
    out['https://eqbench.com/eqbench4/eqbench4_data.js']='const EQBENCH4_DATA='+json.dumps({'generated_at':'2026-09-15','models':[{'display':name,'elo':1500-i*60,'n_scenarios':20} for i,name in enumerate(NAMES)]})+';'
    return out
