import time

import requests


def wait_operation(session, response):
    if response.status_code != 200:
        print(response.json())
    else:
        operation = response.json()['name']
        while True:
            response = session.get(f'https://cloudbuild.googleapis.com/v2/{operation}')
            if response.json()['done']:
                if 'error' in response.json():
                    print(response.json()['error'])
                else:
                    print('success')
                break
            time.sleep(1)


def run(token, region, project, migrate_triggers=False):
    session = requests.session()
    session.headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    cloud_build_v1 = f'https://cloudbuild.googleapis.com/v1/projects/{project}/locations/{region}/gitLabConfigs'
    cloud_build_v2 = f'https://cloudbuild.googleapis.com/v2/projects/{project}/locations/{region}/connections'
    cloud_build_v1_trigger = f'https://cloudbuild.googleapis.com/v1/projects/{project}/locations/{region}/triggers'

    triggers = []
    if migrate_triggers:
        resp = session.get(cloud_build_v1_trigger)
        triggers = resp.json().get('triggers', [])
        triggers = [tr for tr in triggers if 'gitlabEnterpriseEventsConfig' in tr]
        if not triggers:
            print('no triggers found to migrate')

    resp = session.get(cloud_build_v1)
    if len(resp.json().get('gitlabConfigs', [])) == 0:
        print("no connections to migrate")
        return

    for old in resp.json()['gitlabConfigs']:
        name = old['name'].split('/')[-1]
        print(f'start to migrate gitlab connection: {name}')
        webhook_secret = old['secrets']['webhookSecretVersion']
        api_secret = old['secrets']['apiAccessTokenVersion']
        read_secret = old['secrets']['readAccessTokenVersion']
        host = old['enterpriseConfig']['hostUri']
        service_directory = old['enterpriseConfig']['serviceDirectoryConfig']

        data = {
            'gitlab_config': {
                'host_uri': host,
                'webhook_secret_secret_version': webhook_secret,
                'read_authorizer_credential': {
                    'user_token_secret_version': read_secret,
                },
                'authorizer_credential': {
                    'user_token_secret_version': api_secret,
                }
            }
        }
        if 'service' in service_directory:
            data['service_directory_config'] = {
                'service': service_directory['service']
            }
        resp = session.post(f'{cloud_build_v2}?connection_id={name}', json=data)
        wait_operation(session=session, response=resp)
        for repo in old['connectedRepositories']:
            print(f'start to migrate repository: {repo["id"]}')
            repo_name = repo['id'].replace('/', '-')
            remote_uri = f'{host}/{repo["id"]}.git'
            data = {
                'remote_uri': remote_uri
            }
            resp = session.post(f'{cloud_build_v2}/{name}/repositories?repository_id={repo_name}', json=data)
            wait_operation(session=session, response=resp)

            triggers_for_repo = [tr for tr in triggers if
                                 tr['gitlabEnterpriseEventsConfig']['gitlabConfigResource'] == old['name'] and
                                 tr['gitlabEnterpriseEventsConfig']['projectNamespace'] == repo['id']]

            # clear output only fields
            for trigger in triggers_for_repo:
                new_trigger = {key: val
                               for key, val
                               in trigger.items()
                               if key not in {'id', 'createTime', 'resourceName', 'gitlabEnterpriseEventsConfig'}}

                new_trigger['disabled'] = True
                new_trigger['repositoryEventConfig'] = {
                    'repository': f'projects/{project}/locations/{region}/connections/{name}/repositories/{repo_name}'
                }
                new_trigger['name'] = f'{new_trigger["name"]}-v2'

                if 'pullRequest' in trigger['gitlabEnterpriseEventsConfig']:
                    new_trigger['repositoryEventConfig']['pullRequest'] = trigger['gitlabEnterpriseEventsConfig'][
                        'pullRequest']
                else:
                    new_trigger['repositoryEventConfig']['push'] = trigger['gitlabEnterpriseEventsConfig']['push']

                resp = session.post(cloud_build_v1_trigger, json=new_trigger)
                if resp.status_code != 200:
                    print(resp.json())
                else:
                    print(f'new trigger created: {resp.json()["id"]}')


if __name__ == '__main__':
    t = input("Enter your token (you can run gcloud auth print-access-token): ")
    r = input("Enter your region: ")
    p = input("Enter your project ID: ")
    migrate_trigger = input("Do you want to migrate triggers (new triggers will be created in the disabled state): Y/n")
    mt = migrate_trigger == 'Y'
    run(t, r, p, mt)
