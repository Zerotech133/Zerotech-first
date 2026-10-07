import React from "react"
import { Modal, Table, Tag } from "antd"
const { Column } = Table;
type props = {
  visible: boolean,
  setVisible: (visible: boolean) => void,
  data: any,
  copyData:any,
  removeTableList: any,
  removeCombos: (copyData: any) => void,
  modifyTable: () => void,
}
export const RemoveCombos: React.FC<props> = ({ visible, setVisible, modifyTable, data, copyData,removeTableList, removeCombos }) => {
  const handleAddCombosClose = () => {
    removeCombos(copyData)
    setVisible(false)  
  }
  // 点击删除集合
  const deleteCombo = (combo: any, node: any) => {
    modifyTable()
    data.nodes.forEach((item: any) => {
        if (item.id === node.id) {
          let comboIdIndex = item.comboId.findIndex((i: any) => i === combo)
          item.comboId.splice(comboIdIndex, 1)
        }
      })
  }
  const onFinish = (param: any) => {
    removeCombos(data)
    setVisible(false)
  }
  return (
    <>
      <Modal
        title="移出集合"
        visible={visible}
        width={800}
        maskClosable={false}
        centered={true}
        onCancel={handleAddCombosClose}
        onOk={onFinish}
        className="combos"
      >
        <Table 
          dataSource={removeTableList}
          bordered
          size="small"
          pagination={false}
          scroll={{ y: 500 }}
        >
         
          <Column title="节点" dataIndex="label"
            key='label'
            render={(label: string) => (
              <>
                {label.split('\n')[0]}
              </>
            )}
          />
          <Column
            title="集合"
            dataIndex="comboId"
            key="comboId"
            render={(comboId: string[], node: any) => (
              <>
                {comboId.map((tag, index) => (
                  <Tag color="#1890ff" key={index} onClick={() => deleteCombo(tag, node)} className="">
                    <span style={{ marginRight: '10px' }}>{tag}</span>
                    <span>X</span>
                  </Tag>
                ))}
              </>
            )}
          />
        </Table>
      </Modal>
    </>
  )
}
