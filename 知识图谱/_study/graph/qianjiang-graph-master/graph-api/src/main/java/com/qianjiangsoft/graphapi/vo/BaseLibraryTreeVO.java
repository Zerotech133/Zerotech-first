package com.qianjiangsoft.graphapi.vo;

import com.qianjiangsoft.graphcore.dto.BaseProcessLibraryTreeNodeDTO;
import lombok.Data;

import java.util.List;

@Data
public class BaseLibraryTreeVO {

    //关系图谱库
    List<BaseProcessLibraryTreeNodeDTO> relationalGraphTreeNodeDTOList;
}
