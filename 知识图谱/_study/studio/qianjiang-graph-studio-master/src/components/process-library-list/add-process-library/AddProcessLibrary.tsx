import React, {useEffect, useState} from "react"
import {Button, Modal,Form, TreeSelect,AutoComplete, message} from "antd"
import styles from './AddProcessLibrary.module.scss'
import API from '../../../utils/api'
import {handleTree} from '../../../utils/index'

type props = {
  addProcessLibraryVisible: boolean,
  addProcessLibraryData: any
  updateTree: () => void,
  closeAddProcessLibraryModal: () => void
}

export  const AddProcessLibrary: React.FC<props> = ({addProcessLibraryVisible,addProcessLibraryData,updateTree,closeAddProcessLibraryModal}) => {
  const [addVisible, setAddVisible] = useState(false)
  const [form] = Form.useForm();
  const [dataList] = useState<any>([])
  // 树形选择分组名数据
  const [grouptreedata, setGrouptreedata] = useState<any>([])
  // 未处理的分组名数据
  // const [groupTreeDataList, setGroupTreeDataList] = useState<any>([])
  // 分组名id
  const [groupId, setGroupId] = useState('')
  // 过程库名
  const [libraryName, setLibraryName] = useState('')

  let str = false

  const handleOk = () => {
    setAddVisible(false);
  };

  const handleCancel = () => {
    setAddVisible(false);
    closeAddProcessLibraryModal()
  };
  const groupChange = (value:any) => {
    setGroupId(value)
  };

  const nameHandlerChange = (value:any) => {
    setLibraryName(value)
  };
  const onFinish = (value:any) => {
    let param = {
      groupId: groupId === '关联图谱' ? '0' : groupId,
      name: libraryName
    }

    if (addProcessLibraryData) {
      if (addProcessLibraryData.groupId!==groupId || addProcessLibraryData.name!==libraryName) {
        API.editProcess({...addProcessLibraryData,...param}).then((res:any) => {
          message.success('过程库修改成功')
          setAddVisible(false)
          closeAddProcessLibraryModal()
          updateTree()
        })
      } else {
        closeAddProcessLibraryModal()
      }
      
      
    } else {
      API.addProcess({...param,type:'RG'}).then((res:any) => {
        message.success('过程库添加成功')
        setAddVisible(false)
        closeAddProcessLibraryModal()
        updateTree()
      })
    }
    
  };
  

  useEffect(() => {
   setAddVisible(addProcessLibraryVisible)
  }, [addProcessLibraryVisible])

  useEffect(() => {
    API.getGroupList().then((res:any) => {
      console.log(res)
      
      let data = res.data
      data.push({
        id: '关联图谱',
        name: '关联图谱',
        parentId: '过程库',
        groupId: '过程库',
        type: 'RG',
      })
      data.forEach((item:any) => {
        item.groupId = item.parentId === '0' ? '关联图谱' : item.parentId
        item.value = item.id
        item.title = item.name
      })
      let treeData = handleTree(
        data,
        'id',
        'groupId',
        'children',
        '过程库'
      )
      setGrouptreedata(treeData)
      // 编辑过程库
      if (addProcessLibraryData) {
        let id = addProcessLibraryData?.groupId

        setGroupId(id === '0' ? '关联图谱' : id)
        setLibraryName(addProcessLibraryData.name)
      }

    })
   }, [addProcessLibraryData])

  return (
      <div className={styles['addProcesslibrary-container']}>
        <Modal 
        title={addProcessLibraryData?'编辑过程库':'添加过程库'}
        visible={addVisible} 
        width={840}
        footer={null}
        maskClosable={false}
        onOk={handleOk} 
        onCancel={handleCancel}>
          <Form
            form={form}
            labelCol={{ span: 4 }}
            wrapperCol={{ span: 20 }}
            style={{padding:'20px'}}
            onFinish={onFinish}
          >
            <Form.Item label="分组名" name="groupId" rules={[{
              validator: async () => {
                if (!groupId) {
                  return Promise.reject(new Error('请输入分组名'));
                }
              },
            }]}>
              <TreeSelect 
              treeData={grouptreedata}
              onChange={groupChange}
              placeholder={'请选择分组名'}
              style={{ width: '100%' }}
              value={groupId}
              ></TreeSelect>
              {str&&groupId}
            </Form.Item>
            <Form.Item label="过程库名" name="name" rules={[{
              validator: async (_, names) => {
                if (!libraryName) {
                  return Promise.reject(new Error('请输入过程库名'));
                }
              },
            }]}>
            <AutoComplete 
            style={{ width: 200 }} 
            onChange={nameHandlerChange} 
            placeholder="请输入过程库名" 
            value={libraryName}>
              {dataList.map((item: any) => (
                <AutoComplete.Option key={item.id} value={item.name}>
                  {item.name}
                </AutoComplete.Option>
              ))}
            </AutoComplete>
            {str&&<AutoComplete options={dataList}/>}
            
            </Form.Item>
            <br/>
            <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
              <Button  type="primary" htmlType="submit" style={{margin: '0 15px'}}>确定</Button>
              <Button  type="primary" style={{margin: '0 15px'}} onClick={handleCancel}>取消</Button>
            </Form.Item>
          </Form>
        </Modal>
      </div>
  )
}


