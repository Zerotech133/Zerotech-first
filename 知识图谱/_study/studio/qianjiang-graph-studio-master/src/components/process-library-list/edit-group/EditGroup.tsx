import React, {useEffect, useState} from "react"
import { Button, Modal,Form, TreeSelect,AutoComplete, message} from "antd"
import styles from './EditGroup.module.scss'
import API from '../../../utils/api'
import {handleTree} from '../../../utils/index'

type props = {
  addGroupVisible: boolean,
  editGroupData: any,
  updateTree: () => void,
  closeEditGroupModal: () => void
}

export  const EditGroup: React.FC<props> = ({addGroupVisible,editGroupData,updateTree,closeEditGroupModal}) => {
  const [addVisible, setAddVisible] = useState(false)
  const [form] = Form.useForm();
  const [dataList] = useState<any>([])
  // 树形选择分组名数据
  const [grouptreedata, setGrouptreedata] = useState<any>([])
  // 未处理的分组名数据
  // const [groupTreeDataList, setGroupTreeDataList] = useState<any>([])
  // 上级分组
  const [parentId, setParentId] = useState('')
  // 分组名
  const [groupName, setGroupName] = useState('')

  let str = false
  
  const handleOk = () => {
    setAddVisible(false);
  };

  const handleCancel = () => {
    setAddVisible(false);
    closeEditGroupModal()
  };
  const groupChange = (value:any) => {
    setParentId(value)
  };

  const nameHandlerChange = (value:any) => {
    setGroupName(value)
  };
  const onFinish = (value:any) => {
    if (editGroupData) {
      if (editGroupData.parentId!==parentId || editGroupData.name!==groupName) {
        let param = {
          ...editGroupData,
          parentId: parentId === '关联图谱' ? '0' : parentId,
          name: groupName,
        }
        API.editGroup(param).then((res:any) => {
          message.success('分组修改成功')
          setAddVisible(false)
          updateTree()
          closeEditGroupModal()
        })
      } else {
        setAddVisible(false)
        closeEditGroupModal()
      }
    } else {
      let param = {
        parentId: parentId,
        name: groupName,
        type:'RG',
        id:'',
        children:[]
      }
      API.addGroup(param).then((res:any) => {
        message.success('分组名称添加成功')
        setAddVisible(false)
        closeEditGroupModal()
        updateTree()
      })
    }
    
  };
  

  useEffect(() => {
   setAddVisible(addGroupVisible)
  }, [addGroupVisible])

  useEffect(() => {
    API.getGroupList({type:'RG'}).then((res:any) => {
      
      let data = res.data
      
      data.push({
        id: '0',
        name: '关联图谱',
        parentId: '过程库',
        groupId: '过程库',
        type: 'RG'
      })
      data.forEach((item:any) => {
        item.groupId = item.parentId === '0' ? '关联图谱' : item.parentId
        item.value = item.id
        item.title = item.name
        item.rootId = '过程库'
      })

      let treeData = handleTree(
        data,
        'id',
        'parentId',
        'children',
        '过程库',
        editGroupData?.id
      )

      setGrouptreedata(treeData)

      if (editGroupData) {
        let id = editGroupData?.parentId
        setParentId(id === '0' ? '关联图谱' : id)
        setGroupName(editGroupData.name)
      }
    })
   }, [editGroupData])

  return (
      <div className={styles['addProcesslibrary-container']}>
        <Modal 
        title={editGroupData? '编辑分组':'添加分组'}
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
            <Form.Item label="上级分组" name="parentId" rules={[{
              validator: async () => {
                if (!parentId) {
                  return Promise.reject(new Error('请选择上级分组'));
                }
              }
            }]}>
              <TreeSelect 
              treeData={grouptreedata}
              onChange={groupChange}
              placeholder={'请选择上级分组'}
              style={{ width: '100%' }}
              value={parentId}
              ></TreeSelect>
              {str&&parentId}
            </Form.Item>
            <Form.Item label="分组名称" name="name" rules={[{
              validator: async (_, names) => {
                if (!groupName) {
                  return Promise.reject(new Error('请输入分组名称'));
                }
                if(groupName.length > 8) {
                  return Promise.reject(new Error('分组名称不能超过8个字符'));
                }
              },
            },]}>
            <AutoComplete 
            style={{ width: 200 }} 
            onChange={nameHandlerChange} 
            placeholder="请输入分组名称" 
            value={groupName}>
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


