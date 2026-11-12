from app import edgar_client
cik = edgar_client.normalize_cik('0000789019')
subs = edgar_client._submissions(cik)
recent = subs.get('filings',{}).get('recent',{})
forms = recent.get('form',[])
for i,f in enumerate(forms):
    if f == '10-K':
        print(i, recent['filingDate'][i], recent['reportDate'][i], recent['accessionNumber'][i])
        if i > 20: break
