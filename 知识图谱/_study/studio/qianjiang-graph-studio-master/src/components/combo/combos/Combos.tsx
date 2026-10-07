import React, { useState, useEffect } from "react"
import { Button, Modal, Tabs, Form, Input, Select, message } from "antd"
const { TabPane } = Tabs
const { Option } = Select
type props = {
  visible: boolean,
  setVisible: (visible: boolean) => void,
  data: any,
  setCombos: (data: any) => void,
  getCombo: (data: any) => void,
  combosList: any
}

export const Combos: React.FC<props> = ({ visible, setVisible, data, combosList, setCombos, getCombo }) => {
  const [combosListName, setCombosListName] = useState<string[]>([])
  // const [combosList, setCombosList] = useState<object[]>([])
  const [formCreateCmobo] = Form.useForm();
  const [formCombosList] = Form.useForm();
  let content: any
  // 关闭弹窗
  const handleAddCombosClose = () => {
    setVisible(false)
    formCreateCmobo.resetFields();
    formCombosList.resetFields();
  }
  useEffect(() => {
    let allCombo: any = []
    data.nodes.forEach((item: any) => {
      if (item.comboId) allCombo.push(...item.comboId)
    })
    let getAllComboId: any = new Set(allCombo)
    let ComboIdArr = [...getAllComboId]
    let record: any = []
    ComboIdArr.forEach((i: any) => {
      record.push({ id: i })
    })
    setCombosListName(ComboIdArr)
    // setCombosList(record)
  }, [visible, data])
  // 创建集合
  const onFinishCreateCombo = (param: any) => {
    if (param.creatCombo.trim().length) {
      let combo = {
        id: param.creatCombo.trim(),
        label: param.creatCombo.trim()
      }
      if (combosList.length) {
        let filterCombosListId = combosList.filter((item: any) => {
          return item === param.creatCombo.trim()
        })
        if (!filterCombosListId.length) {
          setCombosListName([...combosListName, param.creatCombo])
          content = [...combosList, combo]
          // setCombosList(content)
          setCombos({
            combos: content
          })
        } else {
          message.warning(`集合“${param.creatCombo}”已经存在`)
        }
      } else {
        if (param.creatCombo.trim().length) { }
        content = [combo]
        // setCombosList(content)
        setCombosListName([param.creatCombo])
        setCombos({
          combos: content,
        })
      }
      setVisible(false)
      formCreateCmobo.resetFields();
      formCombosList.resetFields();
    }else{
      message.warning(`请先输入集合名称`)
    }
  }
  // 加入集合
  const onFinishCombosList = (param: any) => {
    getCombo({
      comboId: param.combo,
    })
    formCreateCmobo.resetFields();
    formCombosList.resetFields();
    setVisible(false)
  }
  return (
    <>
      <Modal
        title="加入集合"
        visible={visible}
        footer={null}
        bodyStyle={{ padding: '0px 24px'}}
        width={400}
        className="combos"
        centered={true}
        maskClosable={false}
        onCancel={handleAddCombosClose}
      >
        <Tabs defaultActiveKey="createCombo">
          <TabPane tab="新建集合" key="createCombo" forceRender>
            <Form
              onFinish={onFinishCreateCombo}
              form={formCreateCmobo}
            >
              {/* 边类型 */}
              <Form.Item
                label="集合名称"
                name="creatCombo"
                rules={[{ required: true, message: '集合名称不能为空' }]}
              >
                <Input style={{ width: '100%' }} placeholder="请输入集合名称" />
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
                <Button size='middle' onClick={handleAddCombosClose}>取消</Button>
                <Button size='middle' type="primary" htmlType="submit" style={{ margin: '0 20px' }}>创建</Button>
              </Form.Item>
            </Form>
          </TabPane>
          <TabPane tab="集合列表" key="combosList" forceRender>
            <Form
              form={formCombosList}
              onFinish={onFinishCombosList}>
              {/* 边类型 */}
              <Form.Item
                label="集合列表"
                name="combo"
                rules={[{ required: true, message: '请先选中集合' }]}
              >
                <Select allowClear={true} placeholder="请选择集合">
                  {
                    combosList.map((item: any) => {
                      return (
                        <Option value={item} key={item}>{item}</Option>
                      )
                    })
                  }
                </Select>
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
                <Button size='middle' onClick={handleAddCombosClose}>取消</Button>
                <Button size='middle' type="primary" htmlType="submit" style={{ margin: '0 20px' }}>加入</Button>
              </Form.Item>
            </Form>
          </TabPane>
        </Tabs>
      </Modal>

    </>
  )
}
