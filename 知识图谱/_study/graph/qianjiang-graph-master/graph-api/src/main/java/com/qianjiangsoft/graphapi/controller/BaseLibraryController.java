package com.qianjiangsoft.graphapi.controller;

import cn.hutool.core.bean.BeanUtil;
import com.qianjiangsoft.graphapi.vo.BaseLibraryForSelectVO;
import com.qianjiangsoft.graphcore.dto.BaseLibraryDTO;
import com.qianjiangsoft.graphcore.dto.BaseProcessLibraryTreeNodeDTO;
import com.qianjiangsoft.graphcore.param.BaseLibraryParam;
import com.qianjiangsoft.graphapi.service.BaseLibraryService;
import io.swagger.annotations.Api;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 基础-分组信息Controller
 *
 * @author YoCiyy
 * @date 2021-03-25
 */
@Api(tags = "分组信息")
@Slf4j
@RequestMapping("/base/library")
@RestController
@RequiredArgsConstructor
public class BaseLibraryController {

    @Autowired
    BaseLibraryService baseLibraryService;

    /**
     * 查询基础-库信息列表
     */
    @GetMapping("/queryLibraryListByGroupId")
    @ApiOperation(value = "查询基础-根据分组ID查询库列表信息", response = BaseLibraryForSelectVO.class)
    public List<BaseLibraryForSelectVO> queryLibraryListByGroupId(String groupId) {
        return baseLibraryService.queryLibraryListByGroupId(groupId).stream().map(baseLibraryDTO ->
                BeanUtil.copyProperties(baseLibraryDTO, BaseLibraryForSelectVO.class)).collect(Collectors.toList());
    }

    /**
     * 查询基础-查询库树结构
     */
    @GetMapping("/tree")
    @ApiOperation(value = "查询基础-库 查询库树结构", response = BaseProcessLibraryTreeNodeDTO.class)
    public List<BaseProcessLibraryTreeNodeDTO> getProcessLibraryTree() {
        return baseLibraryService.getLibraryAndGroupTreeNodeList();
    }


    /**
     * 获取基础-库详细信息
     */
    @GetMapping(value = "/{id}")
    public BaseLibraryDTO getById(@PathVariable("id") String id) {
        return baseLibraryService.selectBaseLibraryById(id);
    }

    /**
     * 同一分组名称唯一性校验
     */
    @PostMapping("/checkUnique")
    @ApiOperation("名称唯一性校验")
    public void checkNameExists(@Valid @RequestBody BaseLibraryParam baseLibraryParam) {
        baseLibraryService.checkLibraryNameExists(baseLibraryParam);
    }

    /**
     * 新增基础-分组
     */
    @PostMapping("/add")
    @ApiOperation("新增基础-库信息")
    public void add(@RequestBody BaseLibraryParam param) {
        baseLibraryService.insertBaseLibrary(param);
    }

    /**
     * 修改基础-分组信息
     */
    @PostMapping("/update")
    @ApiOperation("修改基础-过程库信息")
    public void edit(@RequestBody BaseLibraryParam baseLibraryParam) {
        baseLibraryService.updateBaseLibrary(baseLibraryParam);
    }

    /**
     * 修改基础-分组信息
     */
    @PostMapping("/drag")
    @ApiOperation("修改基础-库 拖拽功能")
    public void drag(@RequestParam("id") String id, @RequestParam("groupId") String groupId) {
        baseLibraryService.dragBaseLibrary(id, groupId);
    }

    /**
     * 修改基础-分组信息
     */
    @PostMapping("/drag/batch")
    @ApiOperation("修改基础-库 拖拽功能")
    public void dragBatch(@RequestParam("idList") List<String> idList, @RequestParam("groupId") String groupId) {
        for(String id:idList){
            baseLibraryService.dragBaseLibrary(id, groupId);
        }
    }

    /**
     * 删除基础-分组信息
     */
    @DeleteMapping("/delete/{id}")
    public void remove(@PathVariable String id) {
        baseLibraryService.deleteBaseLibraryById(id);
    }
}
